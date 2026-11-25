# The founder's console

A separate application for the person running the platform. It has its own address (for example `admin.yourcompany.example`), its own sign-in, its own secret and
its own web server, and it cannot be reached from the shop app. Decision record: ADR 0028.

## What is in it

| Screen | What it answers |
|---|---|
| **Sales** | Is the assistant selling? How many chats it handled alone, how many reached an order or visit, wins the owners confirmed, what the safety checks stopped |
| **Growth** | Shops from signing up to paying; trials, churn, support load, new shops by week |
| **Money** | Monthly revenue, average per shop, AI and WhatsApp costs, margin on every shop (worst first) |
| **Health** | Queues, dead letters, open alerts, WhatsApp numbers needing attention, how often the model and sending fail |
| **Agent audit** | Every time the AI was woken: what woke it, how it decided (rules or model), the model cost added up, how long the customer waited, how it ended (including "chose no reply") |
| **Shops & support** | Every business, alerts, support tickets, queues, webhooks, billing, costs |
| **Read a chat** | The one exception to "aggregates only": reading a shop's conversations needs a reason, lasts 5 to 60 minutes, is recorded, and the shop's owner can see it |

A figure the platform does not record yet is shown with a dash and the reason, never as a zero.

## Signing in

You are three settings of the console, fixed by the deployment (ADR 0056): your WhatsApp number (`FOUNDER_PHONE`), your email (`FOUNDER_EMAIL`) and your
authenticator secret (`FOUNDER_TOTP_SECRET`). The database holds none of them, nothing in the running system can change them, and changing one in the
settings ends every session.

The first proof is always the code from your authenticator app (Google Authenticator, Authy, 1Password and similar). How much more it asks depends on the risk:

| The sign-in | It asks for |
|---|---|
| **Routine**: a browser you have signed in from, the usual place, nothing wrong before | the authenticator code |
| **Elevated**: a new browser, or a new place, or a recent lock | the authenticator code, plus **one** code, on your WhatsApp **or** your email (your choice, so a Meta outage never locks you out) |
| **High**: the first sign-in on a database, wrong codes before this one, or a new browser and a new place together | the authenticator code, plus a WhatsApp code **and** an email code |

A WhatsApp or email code is sent only after the authenticator code was right, so guessing can never make the platform message you. Codes work once; the email
code is 8 digits and lasts 10 minutes; five wrong codes of any kind lock the account for 15 minutes; a session lasts 30 minutes (configurable) and is extended only
by a fresh authenticator code. A refresh in the middle of a sign-in keeps its place and sends nothing. There is no "remember me" except a trusted browser (30 days).

Every session is recorded: the address and its network, the device and browser language, the country (and the city and region when your proxy sends them: set
`ADMIN_GEO_HEADER`, `ADMIN_GEO_CITY_HEADER`, `ADMIN_GEO_REGION_HEADER`, for example to Cloudflare's headers), whether the network was the usual one, the risk, every proof
used, when it started, when it was last used, and when and why it ended. The **Security** screen shows all of it and can end any session (it asks for a fresh authenticator code).
You are alerted (High) on every sign-in that needed more than the authenticator.

## Setting it up

1. Make an authenticator secret: `python -m salesai admin-enroll --new-secret`. It prints a QR code and a `FOUNDER_TOTP_SECRET=` line, **once, and stores nothing**. Scan the QR
   with your phone, put the line in `deploy/admin/.env`, and keep a copy of the key in a password manager or printed and locked away: with it a lost phone is not a lost console.
2. Fill in `deploy/admin/.env`: `FOUNDER_PHONE`, `FOUNDER_EMAIL`, `FOUNDER_TOTP_SECRET`, `ADMIN_EMAIL_FROM` (for example `ChatDeal Access <access@yourcompany.example>`, verified in SES),
   the mail settings (`EMAIL_TRANSPORT`, `SES_REGION` and keys, or SMTP), `ADMIN_JWT_SECRET` (must differ from `JWT_SECRET`) and `ADMIN_ALLOWED_ORIGINS` (the console's own address). Then `scripts/stack.sh admin`.
   There is no enrolment step for the database: the first sign-in creates your account row from the settings.
3. Put a TLS proxy (or let Caddy get a certificate for a real hostname) in front of `ADMIN_PORT` on its own subdomain. Optionally restrict who can reach it with `ADMIN_ALLOWED_IPS` (checked by the API on every request) and `ALLOW_IPS` (checked by Caddy).

The `.env` now holds the key to your console: protect it like the master key (file permissions, never in git, never in chat).

To replace a lost phone: add the saved key to a new authenticator app (or run `python -m salesai admin-enroll` to show the QR again). To change the key, make a new one with `--new-secret`, put it
in the settings and restart: every session ends at once. `python -m salesai admin-enroll` (without options) also starts your sign-in state afresh: every session ends and every trusted browser
is forgotten, and the next sign-in is a High one.

## What it deliberately does not do

- It never shows a customer's words, name or number, or a floor price, on any board or in the audit.
- "Sign in as a shop" is not in the console. To help an owner, use a recorded read of the chat, or ask them to show you.
- Passkeys are not built yet (the decision accepts an authenticator app); there are no recovery codes (the saved key, or a new one in the settings, is the recovery).

## Local development

`scripts/dev_env.sh` creates a `.env` with the console enabled. Run the API in `all` mode, then `npm run dev:admin` in `frontend` (port 5174). The console's build
(`npm run build:admin`) shares the component library with the owner app and nothing else; a test fails if either app starts importing the other's screens.

## The console's database login

The console uses its own database login, `app_console`, which is the only one that can read the console's sessions, sign-in state and emailed codes (ADR 0043). On a database that already exists, re-run
`db/bootstrap_roles.sql` (it is safe to repeat; pass `-v console_pw=...`) or run `ALTER ROLE app_console LOGIN PASSWORD '...'`, then apply the migrations (they re-apply the grants) and put
`CONSOLE_DATABASE_URL` in `deploy/admin/.env`. The `admin-enroll` command needs it too, because it resets the sign-in state.

