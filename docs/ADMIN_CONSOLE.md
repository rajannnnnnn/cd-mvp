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

Two proofs, in this order: a code sent to your WhatsApp number, then a code from your authenticator app (Google Authenticator, Authy, 1Password and
similar). Codes work once; five wrong ones lock the account for 15 minutes; a session lasts 30 minutes (configurable) and is extended only by a fresh
authenticator code. There is no "remember me".

## Setting it up once

1. Your number must be a platform operator account. Create it with the operator onboarding (or `INSERT INTO accounts (phone, platform_role) ...`).
2. On the server, with the admin part's `.env` loaded, enrol your authenticator. It prints a secret and an `otpauth://` link; type the secret into your app.
   The secret is shown once and never stored in the clear.

```bash
docker compose --env-file deploy/admin/.env -f deploy/admin/docker-compose.yml run --rm api python -m salesai admin-enroll +919999900000
```

3. Fill in `deploy/admin/.env` (`ADMIN_JWT_SECRET` must differ from `JWT_SECRET`; `ADMIN_ALLOWED_ORIGINS` is the console's own address), then `scripts/stack.sh admin`.
4. Put a TLS proxy in front of `ADMIN_PORT` on its own subdomain. Optionally restrict who can reach it with `ADMIN_ALLOWED_IPS` (checked by the API on every request) and `ALLOW_IPS` (checked by the web server).

To replace a lost phone, run the same enrol command again: it replaces the secret and signs out every console session.

## What it deliberately does not do

- It never shows a customer's words, name or number, or a floor price, on any board or in the audit.
- "Sign in as a shop" is not in the console. To help an owner, use a recorded read of the chat, or ask them to show you.
- Passkeys are not built yet (the decision accepts an authenticator app); there are no recovery codes (re-enrolling on the server is the recovery).

## Local development

`scripts/dev_env.sh` creates a `.env` with the console enabled. Run the API in `all` mode, then `npm run dev:admin` in `frontend` (port 5174). The console's build
(`npm run build:admin`) shares the component library with the owner app and nothing else; a test fails if either app starts importing the other's screens.

## The console's database login

The console uses its own database login, `app_console`, which is the only one that can read the authenticator secrets and sessions (ADR 0043). On a database that already exists, re-run
`db/bootstrap_roles.sql` (it is safe to repeat; pass `-v console_pw=...`) or run `ALTER ROLE app_console LOGIN PASSWORD '...'`, then apply the migrations (they re-apply the grants) and put
`CONSOLE_DATABASE_URL` in `deploy/admin/.env`. The `admin-enroll` command needs it too, because it writes the authenticator secret.

