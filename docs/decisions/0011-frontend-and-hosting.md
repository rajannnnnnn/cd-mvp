# 0011 Client-side-rendered app plus static marketing site; static hosting

**Status:** accepted · 2026-10-04

## Context
The founder asked: CSR or SSR, and CloudFront-style static hosting? The app is a logged-in, per-tenant, live-updating tool
used on low-end Android phones; the marketing site must be fast, indexable and honest.

## Options
SSR framework (Next.js) for everything · SPA for everything · **static marketing pages + SPA for the app**.

## Decision
- **Marketing site**: plain static HTML pages (landing, privacy, terms, data deletion) with a little TypeScript for the
  hero conversation, the live price-limits demo and the EN/HI toggle. Complete HTML with no client rendering needed;
  shared header/footer are inlined at build time.
- **App** under `/app`: React 18 + TypeScript + Vite, client-side rendered. Nothing in it benefits from SSR (it is
  behind a login, personal, and updates live), and CSR makes the whole frontend a folder of static files.
- One Vite multi-page build: `/`, `/privacy.html`, `/terms.html`, `/data-deletion.html`, `/app/*` (history fallback).
- TanStack Query (cache, refetch on focus), generated typed client from `openapi.json` (`openapi-typescript` +
  `openapi-fetch`), refresh-token rotation serialized across tabs with Web Locks, live updates by SSE over `fetch`.
- The API location is **not baked in**: the SPA reads `/config.json` at runtime (empty = same origin).
- English and Hindi; strings default to English in code with a Hindi dictionary and fallback. Self-hosted fonts.
- Strict CSP at the proxy (`script-src 'self'`), no inline scripts.

## Consequences
The frontend can be hosted on S3 + CloudFront, Netlify, or the bundled Caddy container with no code change: the build
output is static files; `/api` and `/webhooks` are routed to the backend by the CDN/proxy (Caddyfile shows the rules).
Cost: a CDN in front of static files is cheap and fast in India; SEO for the marketing pages is unaffected.

## How to revisit
If public, personalised pages appear (e.g. per-shop public catalogs), add SSR/SSG for those routes only.
