# Going live with WhatsApp

Checked against Meta's documentation on 2026-10-31. Meta changes these rules often and publishes no review timelines, so
re-read the linked pages before each step. The code side of every step below already exists unless a step says otherwise.

## 1. What you are applying for

ChatDeal acts for many businesses, so it is a **Tech Provider** on the WhatsApp Business Platform: one Meta app owned by
us, and each business grants that app access to its own WhatsApp Business Account (WABA) through **Embedded Signup**.
Each business keeps its own number and pays Meta's message fees itself, by adding a card to its WABA.

## 2. Webhooks are signed, not encrypted

Meta sends webhooks over HTTPS and signs each one: the `X-Hub-Signature-256` header is an HMAC-SHA256 of the **raw request
bytes**, keyed with our **app secret**. There is nothing to decrypt. The `receive` container verifies the signature before
it stores anything (`verify_signature` in `modules/channels/whatsapp.py`). A separate **verify token**, which we invent,
is only used once, when Meta first checks that the callback URL is ours.

## 3. The credentials, and where each one lives

| Value | Where it comes from | Environment variable | Used by |
|---|---|---|---|
| App ID | App Dashboard > App settings > Basic | `META_APP_ID` | dashboard (Embedded Signup), backend |
| App secret | same page ("App secret") | `META_APP_SECRET` | `receive` (signature check), code exchange |
| Verify token | we choose 32+ random characters | `META_VERIFY_TOKEN` | `receive` (the one-time handshake) |
| Embedded Signup configuration ID | Facebook Login for Business > configuration | `META_CONFIG_ID` | dashboard |
| Graph API version | the version your app shows (versions expire after about two years) | `META_GRAPH_VERSION` | everything that calls Meta |
| Master key | we generate: `openssl rand -base64 32` | `MASTER_KEY` | wraps the per-business keys that encrypt tokens. **Back it up: if it is lost, every business must reconnect.** |
| Business token (one per business) | Embedded Signup, exchanged on our server | not an env value: stored encrypted in the database | `send` |
| Platform number and token (login codes, alerts) | our own WABA | `PLATFORM_SENDER_PHONE_NUMBER_ID`, `PLATFORM_WA_ACCESS_TOKEN` | gateway `send` (ADR 0025: the web part and the agent no longer send) |

Paste the first five into `deploy/gateway/.env` (template: `deploy/gateway/.env.example`). Never put the app secret in the
frontend or in git.

## 4. The steps

### A. Prepare (nothing to do in code)
1. A Meta **business portfolio** for the company.
2. **Business verification** with Meta. It is required before app review. You need the business name, address, phone,
   email and website, a way for Meta to confirm the connection, and documents if Meta cannot find the business.
3. A public website with a **privacy policy URL**, an app icon and an app category.
4. A production domain with a **valid TLS certificate** for the webhook (self-signed certificates are refused). Mutual TLS
   is optional and is set per app.

### B. Create the app and verify the webhook (development mode)
1. In the [App Dashboard](https://developers.facebook.com/), create an app with the **WhatsApp** use case and connect it to
   the portfolio.
2. While the app is in development mode you can use Meta's test number and test WABA with your own admins, developers and
   testers. No review is needed for this.
3. Start the gateway (`scripts/stack.sh gateway`, or `scripts\stack.ps1 gateway`; `deploy/README.md`) and expose `receive` on a public HTTPS address, for example
   `https://hooks.example.com/webhooks/whatsapp`. A tunnel with a valid certificate also works for testing.
4. App Dashboard > WhatsApp > **Configuration** > Edit: enter the callback URL and the verify token. Meta sends a `GET` with
   `hub.mode=subscribe`, `hub.verify_token` and `hub.challenge`; `receive` echoes the challenge only when the token matches.
5. Subscribe these fields: `messages`, `account_update`, `phone_number_quality_update`, `account_review_update`,
   `smb_message_echoes` (numbers that also use the WhatsApp Business app). Also subscribe `message_template_status_update`
   so Meta's template decisions are kept; the code stores it but does not act on it yet.
6. Use the dashboard's test events, then check that rows appear in `webhook_events`.

### C. Become a Tech Provider (App Review)
1. Business verification must be complete.
2. App settings: icon, privacy policy URL, category.
3. Record two videos: a message created in the app and received in WhatsApp, and a message template being created. Meta
   also accepts a recording of the API Setup script and of WhatsApp Manager.
4. Request **Advanced access** to `whatsapp_business_messaging` and `whatsapp_business_management`, then **Begin App Review**.
5. Meta states no fee and no timeline. Until approval, only your own admins, developers and testers can complete signup.

### D. Embedded Signup (version 4)
Versions 2 and 3 were retired on 2026-10-15. Build on **v4**.
1. Facebook Login for Business > Settings: enable Client OAuth, Web OAuth and Enforce HTTPS.
2. Add the dashboard domain to **Allowed domains** and **Valid OAuth redirect URIs**.
3. Create a configuration with the **WhatsApp Embedded Signup** variation and save its ID as `META_CONFIG_ID`.
4. Subscribe to the `account_update` webhook: it fires when a business finishes the flow.
5. What happens when an owner taps *Connect WhatsApp* in Settings:
   - Meta's popup lets the owner pick or create a WABA and number and **grant our app permission**. This is the consent
     each business gives.
   - Our page receives a one-time `code` (valid for 30 seconds) and the `waba_id` and `phone_number_id`.
   - Our backend exchanges the code for that business's token, subscribes our app to the business's WABA
     (`POST /{waba_id}/subscribed_apps`), registers the number and stores the token encrypted
     (`modules/channels/embedded_signup.py`).
   - A number already used in the WhatsApp Business app is onboarded as a coexistence number
     (`FINISH_WHATSAPP_BUSINESS_APP_ONBOARDING`).
6. Each business adds a payment card to its WABA, or Meta blocks sending.

### E. Before the first real customer
- [ ] `APP_ENV=production`. Startup refuses the simulator, any-code sign-in, test payments, the local model and
      placeholder secrets.
- [ ] TLS is valid on the public webhook address, and only `receive` is reachable from outside.
- [ ] Verify token matches on both sides, and the handshake succeeded.
- [ ] All fields in step B5 are subscribed.
- [ ] A WhatsApp **authentication template** for login codes is approved on our own WABA.
- [ ] `MASTER_KEY` is backed up.
- [ ] The monitor shows the gateway healthy (webhook latency and queue age).
- [ ] A test message from your own phone gets a reply, and a status update arrives.

## 5. Rules that shape the product

- **Retries:** if `receive` does not answer 200, Meta retries immediately and then less often for 7 days, then drops the
  event. There is no way to fetch old webhooks, so every webhook is saved as it arrives and duplicates are ignored.
- **Batches:** one request can hold up to 1,000 updates.
- **The 24-hour window:** free-form messages are allowed only while a customer's message from the last 24 hours is open.
  Outside it only approved templates can be sent. `send` enforces this.
- **Limits and quality:** Meta caps how many customers a business can message outside the window, and rates each number's
  quality; low quality reduces throughput.
- **Pricing:** Meta's pricing page says that from 2026-10-01 replies inside the window are billed per message. Check the
  current page: the PRD currently assumes only templates are billed.

## Sources
- [Managing webhooks](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/manage-webhooks/)
- [Create a webhook endpoint](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/create-webhook-endpoint)
- [Become a Tech Provider](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/get-started-for-tech-providers)
- [WhatsApp app review](https://developers.facebook.com/documentation/business-messaging/whatsapp/solution-providers/app-review)
- [Embedded Signup implementation](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/implementation/)
- [Onboard WhatsApp Business app users](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users/)
- [Service messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages)
- [Pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
