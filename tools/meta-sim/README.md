# Meta simulator and phones (a stand-in for Meta, for trying the product before you have Meta credentials)

**Separate from the product.** Its own folder, its own process or container, no imports in either direction, nothing of it in the product's deployed parts.
The product reaches it only through its normal settings (the Meta address, app id and secret, tokens, the browser sign-up script), and it reaches the
product only through the webhook address you give it, signing every webhook as Meta does. To go live with real Meta, change those settings back; nothing in the product changes.

## What it is
Meta (the Graph API and signed webhooks, the sign-up popup, template store, the 24-hour rule, errors you can inject) **and the phones**: any number is a phone with its own chats.

* **Phone view** (default): pick or add any phone number. It has a chat with every WhatsApp number in the simulator: each shop's number and the **platform sender**, which is where the product's
  **sign-in codes arrive** (a "Latest sign-in code" bar with a Copy button, as phone auto-fill would offer). Chat as that phone: text, voice notes and photos (pick a file), "3 fragments".
* **Business view**: pick a shop's number, see its customers, and switch to **Owner (Business app)** to type as the shop owner on the Business app (the product receives these as echoes: coexistence).
* **Audit tab**: for the selected phone (and optionally one business number): a summary (messages, sign-in codes delivered, product acknowledgement time, first-reply time, webhook failures) and a row per message with sent/delivered/read times and what the product answered to the webhook. Download JSON or CSV.
* **Wire log, Timing, Settings**: every webhook and API call, per-message timing, delivery behaviour (offline phone, read receipts), failure injection, account events (disconnect, reconnect), webhook verification like Meta's dashboard,
  and a **Settings for the product** block with the exact variables to copy.

## Run it
```
cd tools/meta-sim
pip install -r requirements.txt
python -m metasim --port 9100 --webhook-url http://127.0.0.1:8001/webhooks/whatsapp --app-secret <META_APP_SECRET> --verify-token <META_VERIFY_TOKEN>
```
or in Docker: copy `.env.example` to `.env`, then `docker compose --env-file .env up -d --build` (settings `SIM_*`, no code changes). Open http://127.0.0.1:9100/.

## Point the product at it (settings only; the Settings tab prints these)
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
