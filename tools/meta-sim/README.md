# Meta simulator and WhatsApp test frontend

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
