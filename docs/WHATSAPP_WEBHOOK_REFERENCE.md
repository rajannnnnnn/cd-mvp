# WhatsApp Cloud API — webhook and messaging reference (with coexistence)

**What this is:** one place that shows, in order, everything that crosses the wire between Meta and ChatDeal: authentication,
the webhook handshake, an incoming customer message and what we do with it, an outgoing message and what Meta sends back, and
the coexistence flow (the owner replying from the WhatsApp Business app). Payloads are Meta's own examples, copied from their
developer documentation, not written from memory.

**Retrieved:** 2026-10-31 from `developers.facebook.com/documentation/business-messaging/whatsapp/` (sources at the bottom).
Graph API version in Meta's current examples: `v26.0`. Our default: `META_GRAPH_VERSION` in `.env` (example file says `v26.0`).

**How to read it**
- **Meta →** = Meta calls us (webhook). **→ Meta** = we call Meta (Graph API).
- 📄 = taken from Meta's documentation. 🔧 = what *our* code does today (file named). ⚠️ = a mismatch or gap found while writing this
  (all listed in section 10).
- Meta's JSON examples use US sample numbers (`15550783881` business, `16505551234` customer). Real Indian numbers start `91…`.
- Numbers in `from`, `wa_id`, `recipient_id` are digits only, no `+`. A `+` is accepted on **send** (`to`).

---

## 1. Authentication — who proves what to whom

| # | Direction | What | Credential | Where it is checked |
|---|---|---|---|---|
| 1 | Meta → us (GET) | Webhook subscription handshake | `hub.verify_token` — a string **we** invent and paste into the Meta app dashboard | 🔧 `ingress.verify_handshake` (constant-time compare, `META_VERIFY_TOKEN`) |
| 2 | Meta → us (POST) | Every webhook event | `X-Hub-Signature-256: sha256=<HMAC-SHA256 of the raw body, key = app secret>` | 🔧 `whatsapp.verify_signature` (`META_APP_SECRET`); raw bytes, never re-serialised |
| 3 | us → Meta | Every Graph API call | `Authorization: Bearer <access token>` | Meta. 🔧 token per business, stored encrypted (INV-11), opened only by the `send` part |
| 4 | Browser → us → Meta | Embedded Signup: turn the one-time `code` into a business token | `GET /oauth/access_token?client_id=<APP_ID>&client_secret=<APP_SECRET>&code=<CODE>`. The code lives **30 seconds** | 🔧 `embedded_signup.complete_signup` |
| 5 | us → Meta | Subscribe our app to a customer's account | `POST /<WABA_ID>/subscribed_apps` with the business token | 🔧 same function |

📄 Token types (Meta): **System User** token (direct developer, long-lived); **Business Integration System User** token (what a
Tech Provider / partner like us gets per customer from the code exchange). Tokens are opaque strings: never parse them. Needed
permissions: `whatsapp_business_messaging` (messages + calls webhooks) and `whatsapp_business_management` (all other webhooks),
plus `business_management` for system tokens.

📄 Optional extras Meta offers: **mutual TLS** on the webhook endpoint (enable per app), and a published list of Meta's webhook IP
ranges (they change; Meta recommends mTLS instead of an IP allow-list). The endpoint needs a valid public TLS certificate
(self-signed is not supported).

---

## 2. Meta → us: the handshake (GET)

📄 Sent every time you set or edit the **Callback URL** or **Verify token** in the App Dashboard.

```
GET <CALLBACK_URL>
  ?hub.mode=subscribe
  &hub.challenge=1158201444
  &hub.verify_token=<your verify token>
```

📄 **Correct response:** HTTP `200` with the `hub.challenge` value as the body. Anything else → Meta treats the endpoint as unverified
and sends nothing.

🔧 **Our response** (`/webhooks/whatsapp`, `routes/system.py`): `200` + challenge as plain text when `hub.mode=subscribe` and the
token matches; otherwise `403 forbidden`.

---

## 3. Meta → us: every event (POST) — the envelope

📄 Request:

```
POST <CALLBACK_URL>
Content-Type: application/json
X-Hub-Signature-256: sha256=<SHA256_PAYLOAD_HASH>
Content-Length: <bytes>

<JSON_PAYLOAD>
```

📄 **Delivery rules**
- Reply `200` to accept. Any other status (or a timeout) → Meta retries immediately, then with decreasing frequency **for up to 7 days**.
  Unacknowledged events are dropped after 7 days. Retries can create **duplicates**: de-duplicate on the message id (`wamid`).
- Events are **batched, up to 1000 updates per POST**; batching is not guaranteed, so handle one or many. Loop over `entry[]`,
  `changes[]` and `messages[]` / `statuses[]`.
- Payloads can be up to **3 MB**.
- There is **no API to fetch past webhooks**. Store what you receive.
- Do not rely on order: Meta states no ordering guarantee for sending, and history chunks explicitly arrive out of sequence.
- Some webhooks are not sent while the app is in **Dev** mode: use **Live** mode.

📄 **Common envelope** (every field value; `field` says which kind):

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "<WHATSAPP_BUSINESS_ACCOUNT_ID>",
      "changes": [
        {
          "value": { "...": "depends on field" },
          "field": "messages"
        }
      ]
    }
  ]
}
```

Account-level events (`account_update`) also carry `"time": <unix seconds>` on the `entry`.

🔧 **What we do on receipt** (`ingress.accept_webhook`, NFR-1, INV-6 — no model, no Meta call, no business logic in this path):

| Step | Result returned to Meta |
|---|---|
| Body larger than 1,000,000 bytes | `413 {"error":"payload too large"}` (not stored) |
| Signature missing or wrong | `401 {"error":"invalid signature"}`; the request is **kept as evidence** (flagged invalid, body cut to `INVALID_WEBHOOK_MAX_BYTES`, short retention), **never queued** |
| Signed but not valid JSON | `400 {"error":"invalid json"}`; kept, flagged, never queued |
| JSON but `object` is not `whatsapp_business_account` | `400 {"error":"unexpected payload"}` |
| **Valid** | exact bytes + signature + payload stored in `webhook_events`; one `webhook.received` job written to the outbox in the **same transaction**; **`200 {"ok": true}`** |

Everything else happens afterwards, off the request path (`inbound` worker → `InboundRouter` → `InboundHandler`).

---

## 4. Incoming customer message  (field `messages`, array `messages[]`)

### 4.1 Meta → us: text message  📄

```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "102290129340398",
      "changes": [
        {
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "15550783881",
              "phone_number_id": "106540352242922"
            },
            "contacts": [
              {
                "profile": { "name": "Sheena Nelson" },
                "wa_id": "16505551234"
              }
            ],
            "messages": [
              {
                "from": "16505551234",
                "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA=",
                "timestamp": "1749416383",
                "type": "text",
                "text": { "body": "Does it come in another color?" }
              }
            ]
          },
          "field": "messages"
        }
      ]
    }
  ]
}
```

**Field guide** (📄): `metadata.phone_number_id` identifies **which of our connected numbers** was messaged (this is how we find the
business). `contacts[].wa_id` / `profile.name` describe the customer; the name is whatever the customer set in WhatsApp.
`messages[].id` is the `wamid` (de-duplication key and the id used for reply-context, read receipts and reactions). `timestamp` is a
string of Unix seconds. `type` selects which sibling object holds the content.

**Optional blocks on a text message** (📄, all documented, none always present):

```jsonc
"context": {                        // user tapped "Message business" on a catalog item
  "from": "15550783881",
  "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgARGA9wcm9kdWN0X2lucXVpcnkA",
  "referred_product": { "catalog_id": "194836987003835", "product_retailer_id": "di9ozbzfi4" }
},
"context": { "forwarded": true },   // forwarded 5 times or fewer
"context": { "frequently_forwarded": true },  // forwarded more than 5 times
"referral": {                       // arrived through a Click-to-WhatsApp ad
  "source_url": "https://fb.me/3cr4Wqqkv", "source_id": "120226305854810726", "source_type": "ad",
  "body": "Summer Succulents are here!", "headline": "Chat with us", "media_type": "image",
  "image_url": "https://scontent.xx.fbcdn.net/v/t45.1...", "ctwa_clid": "Aff-n8ZTODiE79d22K...",
  "welcome_message": { "text": "Hi there! Let us know how we can help!" }
}
```
Also in `contacts[]`: `identity_key_hash`, only if the "identity change check" feature is on. `ctwa_clid` is omitted for WhatsApp Status
ads. A reply to something we sent carries `context.id` = the `wamid` of our message.

### 4.2 Other incoming types  📄 (the `messages[]` object differs per `type`)

```jsonc
// image  (caption optional)
{ "from": "16505551234", "id": "wamid.…", "timestamp": "…", "type": "image",
  "image": { "caption": "Black Prince echeveria", "mime_type": "image/jpeg",
             "sha256": "3f9d94d399fa61c1…", "id": "24230790383178626", "url": "<MEDIA_ASSET_URL>" } }

// audio / voice note  ("voice": true when recorded in the app)
{ "from": "16505551234", "id": "wamid.…", "timestamp": "…", "type": "audio",
  "audio": { "mime_type": "audio/ogg; codecs=opus", "sha256": "…", "id": "<MEDIA_ID>", "url": "<URL>", "voice": true } }

// reaction  (emoji omitted when the user removes the reaction)
{ "from": "16505551234", "id": "wamid.…", "timestamp": "…", "type": "reaction",
  "reaction": { "message_id": "<wamid of the message reacted to>", "emoji": "👍" } }

// interactive: reply to a list OR a button message of ours (+ top-level "context" = our message)
{ "context": { "from": "15550783881", "id": "<wamid of our interactive message>" },
  "from": "16505551234", "id": "wamid.…", "timestamp": "…", "type": "interactive",
  "interactive": { "type": "button_reply", "button_reply": { "id": "<BUTTON_ID>", "title": "<LABEL>" } } }
//   or  "interactive": { "type": "list_reply", "list_reply": { "id": "<ROW_ID>", "title": "<ROW_TITLE>", "description": "<…>" } }
//   or  "interactive": { "type": "product", "product": { "retailer_id": "…", "title": "…", "currency_code": "…", "price_amount": 0, "url": "…" } }

// unsupported: a type WhatsApp cannot pass on. Carries errors[] instead of content.
{ "from": "16505551234", "id": "wamid.…", "timestamp": "…", "type": "unsupported",
  "errors": [ { "code": 131060, "title": "…", "message": "…", "error_data": { "details": "…" } } ],
  "unsupported": { "type": "<UNSUPPORTED_TYPE>" } }
```

Other `type` values documented by Meta: `video`, `document`, `sticker`, `location`, `contacts`, `button` (template quick-reply),
plus `edit` and `revoke` (4.4). (Only the types named in this section were read from Meta's pages; check each type's own reference
before building on one.) **Media arrives as an id (and a `url`), not as bytes.** 🔧 We fetch with
`GET https://graph.facebook.com/<version>/<MEDIA_ID>` (Bearer token) → `{url,…}`, then download that `url` with the same Bearer
header (`CloudApiChannel.download_media`). Media links are short-lived in practice; fetch promptly (this expiry detail is general Meta
behaviour, not re-verified on the pages read here).

### 4.3 🔧 What we do with it  (`whatsapp.parse_webhook` → `inbound.InboundHandler._inbound`)

1. **Find the business:** `metadata.phone_number_id` → `whatsapp_numbers`. Unknown number → `unknown_number` operator alert (INV-12), event dropped.
2. **Owner or customer?** If `from` is a known owner/staff phone, it is an owner chat message (`owner_messages`), not a customer lead.
3. **Customer + conversation** upserted (`customers` by `wa_id`; one conversation per customer per number).
4. **Opt-out / opt-in keywords** are honoured; reactions and personal contacts do not trigger a reply.
5. **Message stored** with `ON CONFLICT (wa_message_id) DO NOTHING` — a redelivery is ignored (INV-6).
6. If a reply is needed: conversation `version` bumped, queued AI replies for this chat are cancelled as superseded, and a
   `message.received` event goes out. The conversation worker then waits for the customer to finish typing (end-of-turn) and runs the agent.
7. Mapped kinds: `text`, `audio`/`voice`, `image`, `reaction`; everything else is stored as `other` (see ⚠️ G3, G5).

### 4.4 Edit and revoke by the customer  📄

A customer editing (within 15 minutes) or deleting (within two days) a message arrives as a `messages[]` entry of `type` `edit` or
`revoke`:

```json
{ "from": "16505551234", "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQUFERjg0NDEzNDdFODU3MUMxMAA=",
  "timestamp": "1749854575", "type": "revoke",
  "revoke": { "original_message_id": "wamid.HBgLMTQxMjU1NTA4MjkVAgASGBQzQUNCNjk5RDUwNUZGMUZEM0VBRAA=" } }
```
```json
{ "from": "16505551234", "id": "wamid.…", "timestamp": "1749854575", "type": "edit",
  "edit": { "original_message_id": "wamid.HBgLMTQxMjU1NTA4MjkVAgASGBQzQUNCNjk5RDUwNUZGMUZEM0VBRAA=",
            "message": { "context": { "id": "M0" }, "type": "image",
                         "image": { "caption": "Updated image caption", "mime_type": "image/jpeg",
                                    "sha256": "a1b2c3d4…", "id": "1234567890", "url": "<URL>" } } } }
```
⚠️ G3: we do not handle these yet.

### 4.5 us → Meta: how we answer the customer  (🔧 `CloudApiChannel`, executed by the `send` part)

**(a) Mark as read + typing indicator** — shown while the agent works; dismissed when we reply or after 25 seconds. 📄
```
POST https://graph.facebook.com/v26.0/106540352242922/messages
Authorization: Bearer <business token>
Content-Type: application/json
```
```json
{ "messaging_product": "whatsapp", "status": "read",
  "message_id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA=",
  "typing_indicator": { "type": "text" } }
```
Meta → us: `200 {"success": true}`. Marking one message read also marks earlier ones. Invalid id → error `131009`.
Only send the typing indicator when you will actually reply.

**(b) The reply** — a free-form text, allowed only inside the 24-hour customer service window. 📄 body shape; 🔧 exact fields we send:
```json
{ "messaging_product": "whatsapp",
  "to": "16505551234",
  "type": "text",
  "text": { "body": "Yes, it is available in red and blue.", "preview_url": false },
  "context": { "message_id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQTRBNjU5OUFFRTAzODEwMTQ0RgA=" } }
```
`context.message_id` makes it a quoted reply (omitted when we are not replying to a specific message). Meta's schema also lists
`"recipient_type": "individual"`; we omit it (Meta treats 1:1 as the default; see G9).

**(c) Meta → us, synchronous response:** see section 5.2.

---

## 5. Outgoing message and Meta's response

### 5.1 us → Meta: send request  📄

```
POST https://graph.facebook.com/<VERSION>/<PHONE_NUMBER_ID>/messages
Authorization: Bearer <token>
Content-Type: application/json
```
```json
{
  "messaging_product": "whatsapp",
  "recipient_type": "individual",
  "to": "+16505551234",
  "type": "text",
  "text": { "preview_url": true, "body": "As requested, here is the link to our latest product: https://www.meta.com/quest/quest-3/" }
}
```
The `type` value names the sibling object that holds the content. Types you can send inside an open window (📄): `text`, `image`,
`audio`, `video`, `document`, `sticker`, `location`, `contacts`, `reaction`, `interactive` (reply buttons, list, CTA URL, flow, location
request, call button, product/catalog), and `address` requests. Outside the window only an approved **template** works.
`messaging_account_id` is only needed when the token reaches more than one Messaging account on the number.
Phone format: always send `+` and country code; without the `+`, Meta prepends **your** business number's country code (can misdeliver).
A reply can quote any earlier message with `"context": {"message_id": "<wamid>"}` (not allowed for reactions or templates).

🔧 We also send: a reaction `{"type":"reaction","reaction":{"message_id":"…","emoji":"…"}}` and a template
`{"type":"template","template":{"name":"…","language":{"code":"…"},"components":[{"type":"body","parameters":[{"type":"text","text":"…"}]}]}}`.

### 5.2 Meta → us: the synchronous API response  📄

**Accepted** (this means *accepted for processing*, **not delivered**):
```json
{
  "messaging_product": "whatsapp",
  "contacts": [ { "input": "+16505551234", "wa_id": "16505551234" } ],
  "messages": [ { "id": "wamid.HBgLMTY0NjcwNDM1OTUVAgARGBI4MjZGRDA0OUE2OTQ3RkEyMzcA" } ]
}
```
`messages[0].id` is the `wamid` that all later status webhooks refer to: **we store it on the outgoing message row**.
For paced templates `messages[0].message_status` may be `accepted`, `held_for_quality_assessment` or `paused`.
`input` is what you sent; `wa_id` is WhatsApp's id for the user and may differ.

**Rejected** (HTTP 4xx/5xx) — the Graph error body:
```json
{
  "error": {
    "message": "(#130429) Rate limit hit",
    "type": "OAuthException",
    "code": 130429,
    "error_data": { "messaging_product": "whatsapp", "details": "Cloud API message throughput has been reached." },
    "error_subcode": 2494055,
    "fbtrace_id": "Az8or2yhqkZfEZ-_4Qn_Bam"
  }
}
```
Build error handling on `code`, not on `error_subcode` (deprecated, absent from v16.0+) or the HTTP status. Keep `fbtrace_id` for support.
Error code table and our mapping: section 8.

🔧 `CloudApiChannel._post`: status < 300 → record `messages[0].id` as the wamid; otherwise `classify_error(code, status, message)` →
permanent (`window_closed`, `token_invalid`, `restricted`, `undeliverable`, `other`) or retryable (rate limits, 5xx, network).

### 5.3 Meta → us: status webhooks (the real delivery outcome)  📄

Same envelope, `field: "messages"`, but `value` holds `statuses[]` instead of `messages[]`. **One outgoing message can produce up to
three status webhooks** (`sent`, `delivered`, `read`), and `failed` replaces them if delivery fails. They don't contain the message
content, only its status.

**Schema**
```jsonc
{
  "object": "whatsapp_business_account",
  "entry": [{ "id": "<WABA_ID>", "changes": [{ "field": "messages", "value": {
    "messaging_product": "whatsapp",
    "metadata": { "display_phone_number": "<BUSINESS_DISPLAY_PHONE_NUMBER>", "phone_number_id": "<BUSINESS_PHONE_NUMBER_ID>" },
    "statuses": [{
      "id": "<WHATSAPP_MESSAGE_ID>",
      "status": "<STATUS>",                      // sent | delivered | read | played | failed
      "timestamp": "<WEBHOOK_TRIGGER_TIMESTAMP>",
      "recipient_id": "<USER_PHONE_NUMBER_OR_GROUP_ID>",
      "recipient_type": "group",                 // only for group messages
      "recipient_participant_id": "<…>",         // only for group messages
      "recipient_identity_key_hash": "<…>",      // only if identity-change check is on
      "biz_opaque_callback_data": "<…>",         // only if you set it when sending
      "conversation": { "id": "<CONVERSATION_ID>", "expiration_timestamp": "<UNIX>", "origin": { "type": "<CATEGORY>" } },
                                                 // sent + one of delivered/read; OMITTED for v24.0+ unless a free-entry-point window
      "pricing": { "billable": true, "pricing_model": "PMP", "type": "regular", "category": "service", "subtype": "paid_exempt" },
                                                 // only with sent + one of delivered/read (appears in one or the other, not both)
      "errors": [ { "code": 131050, "title": "…", "message": "…", "error_data": { "details": "…" }, "href": "<ERROR_CODES_URL>" } ]
                                                 // only on failure
    }]
  }}]}]
}
```
**Status values** (📄): `sent` (one grey tick), `delivered` (two ticks), `read` (two blue ticks, shown in an open chat), `played`
(first play of a voice message), `failed` (red triangle). "Read" implies delivered; when a message is delivered and read at the same
moment the `delivered` webhook is **not** sent. `pricing.pricing_model` is `PMP` (per-message pricing; `CBP` only on webhooks sent
before 1 July 2025). `pricing.type`: `regular`, `free_customer_service`, `free_entry_point`, `free_group_customer_service`.
`pricing.category`: `authentication`, `marketing`, `marketing_lite`, `utility`, `service`, `referral_conversion`, group variants.
`billable` will be deprecated in favour of `pricing.type` + `pricing.category`.

**Example: delivered** 📄
```json
{
  "object": "whatsapp_business_account",
  "entry": [ { "id": "102290129340398", "changes": [ { "value": {
    "messaging_product": "whatsapp",
    "metadata": { "display_phone_number": "15550783881", "phone_number_id": "106540352242922" },
    "statuses": [ {
      "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgARGBI3MTE5MjVBOTE3MDk5QUVFM0YA",
      "status": "delivered",
      "timestamp": "1750263773",
      "recipient_id": "16505551234",
      "conversation": { "id": "6ceb9d929c9bdc4f90e967a32f8639b4", "origin": { "type": "service" } },
      "pricing": { "billable": true, "pricing_model": "CBP", "category": "service" }
    } ]
  }, "field": "messages" } ] } ]
}
```
**Example: sent, v24.0+ (no `conversation`, no `pricing` — they come with the delivered webhook)** 📄
```json
{
  "object": "whatsapp_business_account",
  "entry": [ { "id": "102290129340398", "changes": [ { "value": {
    "messaging_product": "whatsapp",
    "metadata": { "display_phone_number": "15550783881", "phone_number_id": "106540352242922" },
    "statuses": [ { "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQUFERjg0NDEzNDdFODU3MUMxMAA=",
                    "status": "sent", "timestamp": "1750030073", "recipient_id": "16505551234" } ]
  }, "field": "messages" } ] } ]
}
```
**Example: failed** 📄
```json
{
  "object": "whatsapp_business_account",
  "entry": [ { "id": "102290129340398", "changes": [ { "value": {
    "messaging_product": "whatsapp",
    "metadata": { "display_phone_number": "15550783881", "phone_number_id": "106540352242922" },
    "statuses": [ {
      "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgARGBI0QUQ2MjA4NEYyRkExNjMyREUA",
      "status": "failed", "timestamp": "1751142888", "recipient_id": "16505551234",
      "errors": [ {
        "code": 131049,
        "title": "This message was not delivered to maintain healthy ecosystem engagement.",
        "message": "This message was not delivered to maintain healthy ecosystem engagement.",
        "error_data": { "details": "In order to maintain a healthy ecosystem engagement, the message failed to be delivered." },
        "href": "/documentation/business-messaging/whatsapp/support/error-codes"
      } ]
    } ]
  }, "field": "messages" } ] } ]
}
```
**Delivery guarantees** (📄): a successful API response is not a delivery. If Cloud API can't deliver it keeps trying for a
**time-to-live of 30 days** (10 minutes for authentication templates), then drops the message; no `delivered` webhook before the TTL =
assume dropped. A `failed` webhook can lag slightly. **Order of delivery is not guaranteed** to match the order of your requests:
to force order, wait for `delivered` before sending the next part.

🔧 **What we do** (`inbound._status`): look up the message by `wamid`; for `failed` store `"<code>: <title>"` and run the
failed-delivery handling (owner alert, number-health effects); otherwise move status forward only (`sent` < `delivered` < `read`; a
late `sent` never overwrites `read`). Unknown ids are checked against `owner_messages`. `played`, `conversation` and `pricing` are
currently ignored (we don't bill from them; fine for v1).

### 5.4 The 24-hour rule  📄
A customer message (or call) opens a **24-hour customer service window**; each new customer message resets it. Inside it you may send
any free-form type. After it closes only approved templates work, otherwise error `131047`. 🔧 We check the window **at send time**
(INV-8) before ever calling Meta, and treat a `131047` from Meta as permanent for that message.

---

## 6. Coexistence — the owner replies from the WhatsApp Business app

📄 Meta calls this "WhatsApp Business app onboarding" (nickname *coexistence*). The same phone number works in the Business app **and**
on Cloud API; Meta keeps both in sync. Requirements: Business app **2.24.17 or newer**; we must be a Solution Partner or Tech Provider.

### 6.1 Onboarding sequence
| Step | Who | Call / event |
|---|---|---|
| 1 | We | Subscribe the app to the extra fields: `account_update`, `history`, `smb_app_state_sync`, `smb_message_echoes` (on top of `messages`) |
| 2 | Browser | Launch Embedded Signup with `extras.featureType = "whatsapp_business_app_onboarding"` (**v5: on by default**, no `featureType` needed) |
| 3 | Customer | Enters their Business-app number → verification code in the Business app → **Confirm** → decides whether to share chat history |
| 4 | Browser → us | Session event `{ data:{ waba_id }, type:"WA_EMBEDDED_SIGNUP", event:"FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING" }` and, in the response callback, the one-time `code` (**30 s**) |
| 5 | We | `GET /oauth/access_token` (code → business token); `POST /<WABA_ID>/subscribed_apps`; **skip number registration** (already registered) |
| 6 | We | Optional check: `GET /<PHONE_NUMBER_ID>?fields=is_on_biz_app,platform_type` → `{"is_on_biz_app":true,"platform_type":"CLOUD_API","id":"…"}` |
| 7 | We, **within 24 hours** | `POST /<PHONE_NUMBER_ID>/smb_app_data` with `{"messaging_product":"whatsapp","sync_type":"smb_app_state_sync"}` (contacts) then `{"sync_type":"history"}` (chats). Each can be done **once**; response `{"messaging_product":"whatsapp","request_id":"<ID>"}`. **If missed, the customer must offboard and redo the flow.** |

🔧 Steps 1–6 exist in `embedded_signup.complete_signup` (verified only against a fake Graph server). Step 7 is **not implemented** (⚠️ G6).

Session-event shapes (📄): success `{ data:{ phone_number_id, waba_id, business_id, … }, type:'WA_EMBEDDED_SIGNUP', event:'FINISH' | 'FINISH_ONLY_WABA' |
'FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING' | 'FINISH_GRANT_ONLY_API_ACCESS' }`; abandoned `{ data:{ current_step }, …, event:'CANCEL' }`;
error `{ data:{ error_message, error_code, session_id, timestamp }, …, event:'ERROR' }`.
⚠️ **Meta states Embedded Signup v2 and v3 were deprecated on 15 October 2026.** Our `frontend/src/features/settings/signup.ts` still launches
with `sessionInfoVersion: '3'` (⚠️ G7).

### 6.2 Meta → us: the owner's own message — `smb_message_echoes`  📄

Sent whenever the business sends a message from the **Business app or a linked companion device** (up to four; not WhatsApp for Windows /
WearOS). It is the only way we learn that the owner answered a customer personally. **Important:** it uses a different array,
`message_echoes[]` (not `messages[]`), carries `to` (the customer), and its `timestamp` is the webhook trigger time. Messages we send
through the API produce *status* webhooks, not echoes, so there is no double counting.

**Schema**
```json
{
  "object": "whatsapp_business_account",
  "entry": [ { "id": "<WABA_ID>", "changes": [ { "value": {
    "messaging_product": "whatsapp",
    "metadata": { "display_phone_number": "<BUSINESS_DISPLAY_PHONE_NUMBER>", "phone_number_id": "<BUSINESS_PHONE_NUMBER_ID>" },
    "message_echoes": [ {
      "from": "<BUSINESS_DISPLAY_PHONE_NUMBER>",
      "to": "<WHATSAPP_USER_PHONE_NUMBER>",
      "id": "<WHATSAPP_MESSAGE_ID>",
      "timestamp": "<WEBHOOK_TRIGGER_TIMESTAMP>",
      "type": "<MESSAGE_TYPE>",
      "<MESSAGE_TYPE>": { "<MESSAGE_CONTENTS>": "…" }
    } ]
  }, "field": "smb_message_echoes" } ] } ]
}
```
`type` values documented: `text`, `image`, `video`, `document`, `revoke`, `edit`.

**Example: text** 📄
```json
{
  "object": "whatsapp_business_account",
  "entry": [ { "id": "102290129340398", "changes": [ { "value": {
    "messaging_product": "whatsapp",
    "metadata": { "display_phone_number": "15550783881", "phone_number_id": "106540352242922" },
    "message_echoes": [ {
      "from": "15550783881", "to": "16505551234",
      "id": "wamid.HBgLMTY0NjcwNDM1OTUVAgARGBIyNDlBOEI5QUQ4NDc0N0FCNjMA",
      "timestamp": "1739321024", "type": "text",
      "text": { "body": "Here's the info you requested! https://www.meta.com/quest/quest-3/" }
    } ]
  }, "field": "smb_message_echoes" } ] } ]
}
```
**Example: owner deletes a sent message (revoke)** 📄
```json
{ "from": "15550783881", "to": "16505551234",
  "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQUFERjg0NDEzNDdFODU3MUMxMAA=",
  "timestamp": "1749854575", "type": "revoke",
  "revoke": { "original_message_id": "wamid.HBgLMTQxMjU1NTA4MjkVAgASGBQzQUNCNjk5RDUwNUZGMUZEM0VBRAA=" } }
```
**Example: owner edits a sent message (edit)** 📄
```json
{ "from": "15550783881", "to": "16505551234",
  "id": "wamid.HBgLMTY1MDM4Nzk0MzkVAgASGBQzQkJERjg0NDEzNDdFODU3MUMxMAA=",
  "timestamp": "1749854620", "type": "edit",
  "edit": { "original_message_id": "wamid.HBgLMTQxMjU1NTA4MjkVAgASGBQzQUNCNjk5RDUwNUZGMUZEM0VBRAA=",
            "message": { "context": { "id": "M0" }, "type": "image",
                         "image": { "caption": "Updated image caption", "mime_type": "image/jpeg",
                                    "sha256": "a1b2c3d4e5f6…", "id": "1234567890",
                                    "url": "https://lookaside.fbsbx.com/whatsapp_business/attachments/?mid=133…" } } } }
```
🔧 **What we do** (`inbound._echo`, PRD owner takeover):
1. Find the customer by `to` against `customers.wa_id`. **Unknown customer → ignored on purpose** (a chat the AI never saw, possibly the owner's
   personal contact; never ingest it).
2. Store it as an outgoing message with `sender = 'owner'`, de-duplicated by `wamid`.
3. **Pause the AI** in that conversation for `owner_pause_minutes` (default 120), reason `owner_reply`; bump the conversation `version`;
   cancel any queued AI replies (INV-7/INV-10); mark waiting customer messages answered.
4. It does **not** open or extend the 24-hour window: 📄 messages sent from the Business app "do not create, extend, or affect Cloud API
   conversation windows or pricing". `last_inbound_at` is untouched.
⚠️ G2 (the `to` format) and G4 (`edit` / `revoke` not applied) below.

### 6.3 Meta → us: history sync — `history`  📄
Sent after step 7 (`sync_type: history`) if the owner agreed to share: up to **180 days** of 1:1 chats (no groups), in three phases
(0: day 0–1, 1: day 1–90, 2: day 90–180), in chunks that may arrive out of order; **a single webhook can contain thousands of
messages, so store first and process asynchronously**. Media comes as `media_placeholder` entries; their content follows in separate
webhooks, only for media from the last ~2 weeks.
```json
{
  "object": "whatsapp_business_account",
  "entry": [ { "id": "102290129340398", "changes": [ { "value": {
    "messaging_product": "whatsapp",
    "metadata": { "display_phone_number": "15550783881", "phone_number_id": "106540352242922" },
    "history": [ {
      "metadata": { "phase": 0, "chunk_order": 1, "progress": 55 },
      "threads": [ {
        "id": "16505551234",
        "messages": [
          { "from": "15550783881", "id": "wamid.…", "timestamp": "1739230955", "type": "text",
            "text": { "body": "Here's the info you requested! https://www.meta.com/quest/quest-3/" },
            "history_context": { "status": "read" } },
          { "from": "15550783881", "id": "wamid.…", "timestamp": "1739230970", "type": "media_placeholder",
            "history_context": { "status": "played" } },
          { "from": "16505551234", "id": "wamid.…", "timestamp": "1739230970", "type": "text",
            "text": { "body": "Thanks!" }, "history_context": { "status": "read" } }
        ] } ]
    } ]
  }, "field": "history" } ] } ]
}
```
`from` = the business number → the business sent it; `from` = the customer → the customer sent it. `to` appears only on
echo-type entries. `history_context.status`: `delivered | error | pending | played | read | sent`. `progress` 100 = finished.
**Owner declined to share:**
```json
{ "messaging_product": "whatsapp",
  "metadata": { "display_phone_number": "15550783881", "phone_number_id": "106540352242922" },
  "history": [ { "errors": [ { "code": 2593109,
    "title": "History sync is turned off by the business from the WhatsApp Business App",
    "message": "History sync is turned off by the business from the WhatsApp Business App",
    "error_data": { "details": "History sharing is turned off by the business" } } ] } ] }
```
🔧 Not parsed (ignored silently) — ⚠️ G6.

### 6.4 Meta → us: contact sync — `smb_app_state_sync`  📄
After step 7 (`sync_type: smb_app_state_sync`), then again whenever the owner adds, edits or removes a contact in the Business app.
```json
"state_sync": [ {
  "type": "contact",
  "contact": { "full_name": "Pablo Morales", "first_name": "Pablo", "phone_number": "16505551234" },
  "action": "add",
  "metadata": { "timestamp": "1738346006" }
} ]
```
`action`: `add` (added or edited) or `remove` (name fields are then absent). Field name: `smb_app_state_sync`. 🔧 Not parsed (G6) — this is
the natural source for "is this chat a personal contact?" that PRD `is_personal` needs.

### 6.5 Meta → us: account lifecycle — `account_update`  📄
```json
{
  "object": "whatsapp_business_account",
  "entry": [ { "id": "2949482758682047", "time": 1739212624, "changes": [ { "value": {
    "event": "PARTNER_REMOVED",
    "waba_info": { "waba_id": "102290129340398", "owner_business_id": "2329417887457253" },
    "disconnection_info": { "reason": "PRIMARY_INACTIVITY", "initiated_by": "SYSTEM" }
  }, "field": "account_update" } ] } ]
}
```
Events seen for coexistence: `PARTNER_REMOVED` (customer disconnected, or system did), `ACCOUNT_OFFBOARDED`, `ACCOUNT_RECONNECTED`
(the last two have only `{ "event": "…" }` inside `value`, no `waba_info`). `disconnection_info.reason`: `ACCOUNT_DISCONNECTED`,
`BUSINESS_DOWNGRADE`, `CHANGE_NUMBER`, `COMPANION_INACTIVITY` (~30 days), `PRIMARY_INACTIVITY` (~14 days), `USER_RE_REGISTERED`;
`initiated_by`: `USER` | `SYSTEM`. **A phone that stays offline ~14 days, or a companion idle ~30 days, disconnects the number.**
The customer can also disconnect in the Business app (Settings → Account → Business Platform → Disconnect Account); you cannot
deregister a coexistence number through the API.
🔧 `whatsapp._account_event` maps `PARTNER_REMOVED / DISABLED_UPDATE / ACCOUNT_DELETED / COEXISTENCE_DISCONNECTED` → "disconnected"
(AI stops, owner and operator alerted), `ACCOUNT_RECONNECTED / RECONNECTED / VERIFIED_ACCOUNT` → "reconnected", restriction events →
"restricted". `ACCOUNT_OFFBOARDED` is **not** in that list (it falls to "other") — ⚠️ G8.

### 6.6 Coexistence rules that affect product behaviour  📄
- **Throughput:** a number used by both apps has a **fixed 20 messages/second** (🔧 our comments assume 5/s; conservative, but see G10).
- **Pricing:** messages from the Business app stay free; messages through Cloud API are billed at Cloud API rates.
- **First message before onboarding:** if a customer wrote just before onboarding, no window exists; only a template can answer.
- **Not supported with Cloud API:** group chats, disappearing/view-once/live-location messages, broadcast lists, calls, catalog/orders
  in-app tools, quick replies, labels, channels. Disappearing messages are switched off for 1:1 chats on onboarding.
- **Unsupported companions:** a customer messaging through an unsupported linked device may not trigger a `messages` webhook at all.
  Expect an `unsupported` message with code **131060** — this is *expected* in coexistence (first message from an ad click, or an
  unsupported companion); it normally resolves in seconds. Tell the owner to check the Business app.
- Edit/revoke by the **owner** work in both directions and arrive as echoes (6.2); edit/revoke by the **customer** arrive as `messages` (4.4).

---

## 7. All webhook `field` values (📄)

`account_alerts`, `account_review_update`, `account_update`, `automatic_events`, `business_capability_update`, `calls`, `history`,
`message_template_components_update`, `message_template_quality_update`, `message_template_status_update`, `messages`,
`partner_solutions`, `payment_configuration_update`, `phone_number_name_update`, `phone_number_quality_update`, `security`,
`smb_app_state_sync`, `smb_message_echoes`, `template_category_update`, `user_preferences`.

🔧 Handled today: `messages` (messages + statuses), `smb_message_echoes`, `account_update`, `phone_number_quality_update`,
`account_review_update`. All others are stored in the raw log and acknowledged, never rejected (Meta adds fields without notice).
Webhook-level problems also arrive as `value.errors[]` on `field: messages` (not tied to one message), e.g. `130429 Rate limit hit`:
```json
"errors": [ { "code": 130429, "title": "Rate limit hit", "message": "Rate limit hit",
  "error_data": { "details": "Message failed to send because there were too many messages sent from this phone number in a short period of time" },
  "href": "/documentation/business-messaging/whatsapp/support/error-codes" } ]
```
Webhook overrides (a different callback per field, WABA or number) exist; we don't use them.

---

## 8. Error codes that matter, and how we treat them

| Code | Meta's meaning (📄) | Our class today (🔧 `whatsapp.py`) | Right? |
|---|---|---|---|
| `0`, `190` | Token invalid / expired | `190` → token_invalid (`0` not mapped) | ✓ (add `0`) |
| `3`, `10`, `200–299` | Permission / capability missing | not mapped → "other" | review |
| `4`, `80007`, `130429` | App / account / **throughput** rate limit | retry after 5 s | ✓ |
| `131056` | Too many messages to the same recipient | retry | ✓ |
| `131048` | Spam-rate restriction on this number | **retry** | ✗ should alert, not retry blindly |
| `131047` | **More than 24 h since the customer last replied** | window_closed | ✓ |
| `131051` | **Unsupported message type** | window_closed | ✗ **wrong** (G1) |
| `131049` | Not delivered to protect ecosystem engagement (marketing cap) | undeliverable | ✓ |
| `131026` | Undeliverable (not on WhatsApp / old client / ToS not accepted) | undeliverable | ✓ |
| `131021` | Sender and recipient are the same number | undeliverable | ✓ |
| `131031`, `368` | Account restricted / locked | restricted | ✓ |
| `131042` | Payment-method problem | restricted | ✓ |
| `131053` | **Media upload error** | restricted | ✗ **wrong** (G1) |
| `131052` | Could not download the media the *user* sent | not mapped | add |
| `131008`, `131009` | Missing / invalid parameter | "other" | ✓ |
| `131045`, `133010` | Number not registered | "other" | review |
| `130497` | Account restricted from messaging to certain countries | not mapped | add |
| `131060` | Unsupported message received (coexistence) | n/a (webhook) | see G5 |
| `2593109` | Owner declined history sharing | n/a (webhook) | see 6.3 |

---

## 9. End-to-end timeline (one customer message, with coexistence)

```
t0   Customer sends "Price of the red saree?"
     Meta → us   POST /webhooks/whatsapp   field=messages, messages[0].type=text            (4.1)
     us → Meta   200 {"ok":true}  — after verifying X-Hub-Signature-256 and storing raw bytes  (3)
t0+  inbound worker: number → business, customer, conversation, message row (dedup by wamid) (4.3)
t0+  send part → Meta  POST /<phone_number_id>/messages {status:read, typing_indicator}      (4.5a)  ← {"success":true}
t0+  agent turn (waits for the customer to finish typing, plans, prices via the engine, writes, checks)
t1   send part checks window + per-number limit + pacing, then
     us → Meta   POST /<phone_number_id>/messages {type:text, context.message_id}           (4.5b)
     Meta → us   200 {messages:[{id:wamid.OUT}]}   (accepted, not delivered)                (5.2)
t1+  Meta → us   statuses[]: sent → delivered → read   (matched by wamid.OUT)                (5.3)

     — now the owner steps in from the Business app —
t2   Owner types a reply in the WhatsApp Business app
     Meta → us   smb_message_echoes: message_echoes[0] {from=business, to=customer}         (6.2)
     us          store as sender=owner; pause AI for 120 min; cancel queued AI replies
t3   Customer replies → new `messages` webhook → stored, but the AI stays silent until the pause ends
     (the owner sees it in the Business app)
```

---

## 10. Gaps found while writing this (against Meta's current documentation)

| ID | Severity | Finding | Where | Suggested fix |
|---|---|---|---|---|
| G1 | **High** | Error `131051` (*unsupported message type*) is classed as `window_closed`; `131053` (*media upload error*) is classed `restricted`. A bad media send would be treated as "outside the 24 h window" / "account restricted". `131048` (spam-rate restriction) is retried instead of alerted. | `whatsapp.py:29-33` | Fix the three sets; add `0`, `3`, `10`, `130497`; test per code |
| G2 | **High** | Echo `to` may arrive with or without `+` (Meta's example shows digits only, Meta's parameter table shows `+16505551234`). We look the customer up with the raw `to` against `customers.wa_id` (digits only). With a `+`, **the owner's reply is silently dropped and the AI keeps talking over the owner.** | `whatsapp.py:115`, `inbound.py:180` | Normalise `to` to digits in `parse_webhook`; test both forms |
| G3 | Medium | Customer `edit` / `revoke` (`messages[].type`) are stored as a new "other" message that needs a reply, so the AI may answer a deletion. | `whatsapp.py:_kind`, `inbound._inbound` | Edit → update original body; revoke → mark deleted; neither triggers a reply |
| G4 | Medium | Owner `edit` / `revoke` echoes are stored as new owner messages with no text instead of changing the original. They do correctly pause the AI. | `inbound._echo` | Apply to `original_message_id` |
| G5 | Low | `unsupported` customer messages (incl. the expected `131060` in coexistence) are treated as ordinary messages needing a reply. | `inbound._inbound` | Record, do not reply; optionally one canned "please type it" |
| G6 | **High** (coexistence go-live) | `smb_app_data` (contacts + history sync) is never called, and `history` / `smb_app_state_sync` are not parsed. Meta requires the sync **within 24 h of onboarding** or the customer must offboard and redo the flow. We also lose the contact list that tells personal chats from customers. | `embedded_signup.py` (missing), `whatsapp.py` | Call both syncs right after onboarding; parse and store asynchronously |
| G7 | **High** | Frontend launches Embedded Signup with `sessionInfoVersion: '3'`; Meta says v2/v3 were deprecated on 15 Oct 2026. v5 turns coexistence on by default. | `frontend/src/features/settings/signup.ts:59` | Migrate to v5 and re-test the callback shape |
| G8 | Low | `ACCOUNT_OFFBOARDED` is not mapped to "disconnected". | `whatsapp.py:131` | Add it |
| G9 | Low | We omit `recipient_type` on send (schema lists it as required, Meta accepts 1:1 without it today). | `CloudApiChannel` | Send `"recipient_type":"individual"` |
| G10 | Info | Code comment says coexistence throughput is 5 msg/s; Meta says a fixed 20 msg/s. Conservative, not wrong. | `ratelimit.py:1` | Make it a setting; confirm on a real number |
| G11 | Info | All test fixtures are synthetic, written from documentation. This document is also from documentation. | `tests/fixtures/meta/` | Record real payloads from the Meta test number |

---

## Sources (all fetched 2026-10-31)

- Webhooks overview — `…/whatsapp/webhooks/overview`
- Create a webhook endpoint — `…/webhooks/create-webhook-endpoint`
- messages webhook reference — `…/webhooks/reference/messages` (+ `/text`, `/status`, `/image`, `/audio`, `/reaction`, `/interactive`, `/unsupported`, `/errors`)
- smb_message_echoes reference — `…/webhooks/reference/smb_message_echoes`
- Onboard WhatsApp Business app users (coexistence) — `…/embedded-signup/onboarding-business-app-users`
- Embedded Signup implementation — `…/embedded-signup/implementation`; onboarding as a Tech Provider — `…/embedded-signup/onboarding-customers-as-a-tech-provider`
- Service messages (sending) — `…/messages/send-messages`; Message API reference — `…/reference/whatsapp-business-phone-number/message-api`
- Mark as read — `…/messages/mark-message-as-read`; Typing indicators — `…/typing-indicators`; Contextual replies — `…/messages/contextual-replies`
- Access tokens — `…/access-tokens`; Error codes — `…/support/error-codes`

All under `https://developers.facebook.com/documentation/business-messaging/whatsapp`. Meta changes this documentation often; re-check
before launch and replace the sample payloads with recordings from a real test number.
