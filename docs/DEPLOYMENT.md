# Deploying ChatDeal: one machine, many machines, and the secrets you need

## 1. How the parts fit together

Five parts, each its own folder under `deploy/` with its own `.env` and its own docker compose project:

| Part | Services | Needs to be reachable from | Holds (secrets) |
|---|---|---|---|
| **db** | `migrate` (a one-off job) | nobody | the schema-owner login |
| **web** | `api`, `scheduler`, `site` | the internet (`:8080`, behind TLS) | `JWT_SECRET`, `OTP_SECRET`, SMTP password |
| **gateway** | `receive`, `inbound`, `send`, `scheduler` | **only `receive`** from the internet (`:8001`), for Meta's webhooks | Meta app secret and verify token, `MASTER_KEY`, the platform's WhatsApp token |
| **agent** | `agent`, `scheduler` | nobody | the model key, the floor-reading database login |
| **admin** | `api`, `site` | you only (`:8082`, behind TLS, ideally an IP allow-list) | `ADMIN_JWT_SECRET`, `MASTER_KEY`, the console's database login |

**The parts never call each other.** They meet in the database (and in Redis if you choose it). So there are no "service addresses" to wire between them. What each part needs is:

- **The database address**, the same server in every part (`DATABASE_URL`, `SYSTEM_DATABASE_URL`, `MIGRATION_DATABASE_URL`; plus `PRICING_DATABASE_URL` only in the agent and `CONSOLE_DATABASE_URL` only in the console).
- **Its own public address**, set where the part needs to know it:
  - web: `ALLOWED_ORIGINS=https://app.yourdomain` (the address people open).
  - admin: `ADMIN_ALLOWED_ORIGINS=https://admin.yourdomain`.
  - gateway: nothing in the file; the address goes into Meta's dashboard as the webhook callback URL (`https://hooks.yourdomain/webhooks/whatsapp`).
- **Inside a part**, services find each other by name (the web site proxies `/api` to its own `api` service). The one cross-part link is optional: the web site can proxy `/webhooks` to the gateway's `receive` (`WEBHOOK_UPSTREAM=host:8001`) so one public address serves both. On many machines, give the gateway its own address instead.
- The same `QUEUE_BACKEND` everywhere (`postgres` by default; `redis` + `REDIS_URL` if you add Redis).

## 2. One machine (what you have now)

**Windows, shortest path** (with a Postgres container named `chatdeal-pg`, or any Postgres you have superuser access to): `scripts\setup-db.ps1` takes the superuser login once, creates the database and the five app roles with fresh passwords, writes the five `deploy\<part>\.env` files (database URLs plus the secrets you generate yourself: JWT, OTP, admin, master key, webhook token), and runs the migrations. The superuser login is used there and stored nowhere. Safe to run again: existing secrets are kept, database passwords are renewed. It is untested until your first run.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-db.ps1 -PgContainer chatdeal-pg -SuperPassword devpass
```

The manual steps below are what it does, and what to do on Linux.

**If Docker on Windows says "error getting credentials ... logon session does not exist"**, run any command through the helper `scripts\nocreds.ps1`, which hides Docker's saved-login helper for that one run: `.\scripts\nocreds.ps1 .\scripts\stack.ps1 db`. The lasting fix is to sign out of Windows and back in.

```bash
# 1. database (Postgres 16, outside Docker) and its roles
psql -f db/bootstrap_roles.sql -v owner_pw=... -v user_pw=... -v pricing_pw=... -v system_pw=... -v console_pw=... <your db>
# 2. fill in deploy/{db,web,gateway,agent,admin}/.env (copy each .env.example). Containers reach the database at host.docker.internal.
# 3. start, in this order
scripts/stack.sh db          # migrations: first, and after every release
scripts/stack.sh all         # web, admin, gateway, agent (or one at a time: web | gateway | agent | admin)
# 4. check what you filled in
python -m salesai doctor     # (run with the same settings; see section 5)
```

Put a TLS proxy (Caddy, Nginx, or a Cloudflare tunnel) in front of three public hostnames: `app.` -> `:8080`, `hooks.` -> `:8001` (or leave Meta's webhook on `app.` and set `WEBHOOK_UPSTREAM`), `admin.` -> `:8082`.

## 3. Many machines, and copies

Nothing in the code is tied to one machine, because the parts only share the database. A typical layout:

```
 machine A   Postgres (+ Redis, optional)        private network only
 machine B   web      (site + api)   [x2 behind a load balancer if you want]    public app.
 machine C   gateway  (receive public; inbound + send)                          public hooks.
 machine D   agent    (the AI)       [x N, add machines to add capacity]        no public port
 machine E   admin    (console)                                                  admin. (your IP only)
```

For each machine:
1. Install Docker, clone the repository (or build the backend image once and push it to a registry; set `BACKEND_IMAGE` to it in each `.env`).
2. Copy **only that part's** `deploy/<part>/.env.example` to `.env` and fill it in. Put only that part's secrets there (that is what the separation is for).
3. Replace `host.docker.internal` in the database URLs with the database machine's private address. Let that address through the database firewall from the part machines only, allow their logins in `pg_hba.conf`, and add `?sslmode=require` to the URLs if the traffic leaves a trusted network.
4. `scripts/stack.sh <part>`.

**Copies (scaling):**
- **agent:** `scripts/stack.sh agent --scale agent=3` on one machine, and/or start the part on more machines. Each copy runs `AGENT_CONCURRENCY` jobs at once. The queue hands a conversation to one worker at a time, so copies never double-reply.
- **gateway:** scale `send` and `inbound` the same way (`--scale send=2`); run several `receive` behind a load balancer (the webhook is stateless).
- **web:** `api` is stateless (signed sessions), so any number of copies behind a load balancer works. Each machine's `site` proxies to its own `api`.
- **schedulers:** run one on every copy of a part if you like. Each part holds a leader lock, so one works and the others stand by.
- **admin:** one is plenty.
- With `QUEUE_BACKEND=postgres` (default) the queue works across machines as is. Redis is optional and must be configured identically everywhere.

**Releases:** run `db` first (changes stay backward compatible for one release), then restart parts in any order, one machine at a time.

## 4. The secrets, and how to get them

**You generate these (nothing to sign up for):**

| Secret | Where | How |
|---|---|---|
| Database passwords (5 roles: `app_owner`, `app_user`, `app_pricing`, `app_system`, `app_console`) | `bootstrap_roles.sql` and each part's database URLs | any strong random string each, e.g. `openssl rand -base64 24` |
| `JWT_SECRET`, `OTP_SECRET` | web (and the same two in admin) | `openssl rand -base64 48` each |
| `ADMIN_JWT_SECRET` | admin only; **must differ** from `JWT_SECRET` | `openssl rand -base64 48` |
| `MASTER_KEY` | gateway **and** admin, the same value | `openssl rand -base64 32`. **Back it up (a password manager plus one offline copy): if it is lost, every shop must reconnect its WhatsApp.** |
| `META_VERIFY_TOKEN` | gateway | make up 32+ random characters; paste the same value into Meta's webhook form |
| Your authenticator for the console | on your phone | `python -m salesai admin-enroll +91XXXXXXXXXX`, scan the code in any authenticator app |

**You obtain these from other services:**

- **Meta values** (`META_APP_ID`, `META_APP_SECRET`, `META_CONFIG_ID`). In developers.facebook.com create an app with the WhatsApp use case: App ID and App secret are under App settings > Basic. `META_CONFIG_ID` comes from Facebook Login for Business > Configurations, a configuration with the "WhatsApp Embedded Signup" variation. Set `META_GRAPH_VERSION` to the version your app shows. This needs a verified Meta business; `docs/WHATSAPP_GO_LIVE.md` has the full path, including the Tech Provider review that gates self-serve signup.
- **The platform's own WhatsApp number** (`PLATFORM_SENDER_PHONE_NUMBER_ID`, `PLATFORM_WA_ACCESS_TOKEN`), used for sign-in codes and alerts. Register a number on your own WhatsApp Business account (WhatsApp Manager > Phone numbers; the number id is shown beside it). For the token, in Business settings > Users > **System users**, add a system user, assign it your WhatsApp account as an asset with full control, and **Generate token** with the permissions `whatsapp_business_messaging` and `whatsapp_business_management` and no expiry. Keep it only in the gateway's `.env`. Sign-in codes and alerts use approved templates, so submit `otp_login`, `handoff_alert`, `deal_alert`, `disconnect_alert` and `usage_warning` (texts in `backend/src/salesai/templates.py`) in WhatsApp Manager > Message templates.
- **SMTP for alert email** (`SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `ALERT_EMAIL_FROM`, `ALERT_EMAIL_TO`). Use any provider: Amazon SES (verify your domain, then create SMTP credentials), Zoho Mail or Brevo (SMTP settings page). Use port 587 with `SMTP_SECURITY=starttls`. The from-address must be one the provider has verified. A personal Gmail is a poor choice (Google blocks automated sending).
- **Domains and TLS:** three hostnames (app, hooks, admin) pointing at your servers, each with a valid certificate (Caddy gets them automatically; Cloudflare tunnels also work). Meta refuses self-signed certificates for the webhook.
- **Optional:** `ALERT_WEBHOOK_URL` (a Slack incoming-webhook URL: Slack > Apps > Incoming Webhooks), `ALERT_WHATSAPP_NUMBERS` (your own number, for Critical only), `REDIS_URL` (a managed Redis, if you use it).
- **Business details for invoices:** `INVOICE_SELLER_NAME`, `INVOICE_SELLER_ADDRESS`, `INVOICE_SELLER_GSTIN`, `PAYMENT_INSTRUCTIONS` (your UPI id or bank details). They come from your registration and GST certificate.

## 5. Checking it

Run `python -m salesai doctor` with each part's settings loaded (inside a container: `docker compose -f deploy/<part>/docker-compose.yml --env-file deploy/<part>/.env run --rm <service> doctor`; this form is untested until your first run). It checks database logins, migrations, the login separation, the model, mail and the Meta token, and names the setting to fix. Each service also answers `/healthz`.

Order for a first go-live: database and roles, `.env` files, `db`, `gateway` (then register the webhook in Meta), `web`, `agent`, `admin` (then enrol your authenticator), `doctor` on each, register your first pilot shop from the console, send a test WhatsApp message. The full checklist is `docs/GO_LIVE_CHECKLIST.md`.
