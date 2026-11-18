# Meta simulator and phones (a stand-in for Meta, for trying the product before you have Meta credentials)

**Separate from the product.** Its own folder, its own process or container, no imports in either direction, nothing of it in the product's deployed parts.
The product reaches it only through its normal settings (the Meta address, app id and secret, tokens, the browser sign-up script), and it reaches the
product only through the webhook address you give it, signing every webhook as Meta does. To go live with real Meta, change those settings back; nothing in the product changes.

## What it is
Meta (the Graph API and signed webhooks, the sign-up popup, template store, the 24-hour rule, errors you can inject) **and the phones**: any number is a phone with its own chats. Built to be easy for QA to follow.

**The two things you switch, always visible:**
* **The phone** (top bar, "You are holding this phone"): each number chip is a different mobile. Add one with "Add phone". A dot on a chip means that phone has unread messages.
* **The chat** (the list on the left, "Chats on this phone"): the conversations of that phone. The top of every chat says **📱 This phone ➜ 💬 talking to**, and the number is always shown under the name, even for a saved contact.

**It behaves like WhatsApp:** numbers only until you "Save contact" (a name that belongs to that one phone), unread badges and bold previews, your message shows at once with a clock and then ✓ ✓✓ blue ✓✓, animated "typing…" dots while the product writes, messages slide in, chats fade when you switch.

**Two modes** (top left): **📱 Customer phones** (default) and **🏪 Shop side**, where you are the shop: pick the shop number, see its customers, and type either as the customer or as the owner's WhatsApp Business app ("As the shop owner": the product sees an echo, i.e. coexistence).

**Sign-in codes** sent by the product arrive on the phone you used to sign in: a yellow bar with a Copy button, as phone auto-fill would offer.

**Right-hand tools** (every log reads top to bottom, oldest first, newest at the bottom):
* **Activity log**: every webhook the simulator sent to the product and every API call it received. Click a row for the raw message.
* **Reply speed**: how fast the product answered each customer message.
* **Report**: a summary and table for the selected phone, downloadable as JSON or CSV for a bug report.
* **Test controls**: make things go wrong on purpose (offline phone, read receipts, a failed send, disconnect/reconnect the shop), check the connection to the product, a **number directory** (which number is which shop), and the exact settings to copy into the product.

The **❓ How this works** button explains all of this in plain words.

## Run it
```
cd tools/meta-sim
pip install -r requirements.txt
python -m metasim --port 9100 --webhook-url http://127.0.0.1:8001/webhooks/whatsapp --app-secret <META_APP_SECRET> --verify-token <META_VERIFY_TOKEN>
```
or in Docker: copy `.env.example` to `.env`, then `docker compose --env-file .env up -d --build` (settings `SIM_*`, no code changes). Open http://127.0.0.1:9100/.

## Point the product at it (settings only; Test controls > "Settings to copy into the product" prints these)
```
# gateway
META_GRAPH_BASE=http://host.docker.internal:9100     # http://127.0.0.1:9100 when the gateway runs on the host
META_APP_ID=sim-app   META_CONFIG_ID=sim-config   META_APP_SECRET=<same as the simulator>   META_VERIFY_TOKEN=<same>
OTP_CHANNEL=whatsapp_cloud   PLATFORM_SENDER_PHONE_NUMBER_ID=sim-platform   PLATFORM_WA_ACCESS_TOKEN=SIMTOKEN-platform
# web (what the browser loads)
META_APP_ID=sim-app   META_CONFIG_ID=sim-config   META_SDK_URL=http://localhost:9100/sdk.js   META_SIGNUP_ORIGINS=http://localhost:9100
```
Then sign up as a shop owner with a phone number you add in Phone view (the code appears on that phone), press **Connect WhatsApp** (the popup is the simulator's: it creates the shop's number),
and chat to that number from another phone. `e2e/drive_local_product.py` does the whole journey through HTTP as a check.

## Tests
`pip install -r requirements.txt pytest && pytest tests` (the simulator alone) and `pytest contract` (the product's own Meta code against it; skips if the product is not installed).

---

## Reference (how the original simulator was described)

Independent of the product: its own folder, its own process, no imports either way. The product talks to it exactly as it would talk to Meta.

```
cd tools/meta-sim
pip install -r requirements.txt
python -m metasim --port 9100 --webhook-url http://127.0.0.1:8001/webhooks/whatsapp --app-secret <META_APP_SECRET> --verify-token <META_VERIFY_TOKEN>
```
Open http://127.0.0.1:9100/ — you are the customer's phone (or, with the toggle, the owner's WhatsApp Business app).

Point the product at it with its normal settings only: `META_GRAPH_BASE=http://127.0.0.1:9100`, the `META_APP_SECRET` / `META_VERIFY_TOKEN`
used above, and a number whose `phone_number_id` and access token are the ones listed in the Settings tab.

| Direction | What the simulator does |
|---|---|
| sim → product | Signed webhooks (`X-Hub-Signature-256`): customer messages (text, voice, photo), statuses (sent, delivered, read, failed), owner echoes (`smb_message_echoes`), `account_update`. Retries a non-200 like Meta. GET handshake via "Verify webhook". |
| product → sim | `POST /{version}/{phone_number_id}/messages` (text, reaction, template…, mark-as-read with typing indicator), `GET /{version}/{id}?fields=`, `oauth/access_token`, `subscribed_apps`, `register`. Meta's real response and error shapes; token check (190), 24-hour window (131047), injected failures (130429, 131049 …). |
| browser | Chat view with ticks and typing, owner mode, wire log of every webhook and API call, timing table, settings, scripted customers (fragments/bursts). Live over Server-Sent Events. |

Payload shapes follow `docs/WHATSAPP_WEBHOOK_REFERENCE.md`. All timings shown are wall-clock through whatever is connected; if the model is the
LLM bridge, they are not product latency.
