# Build the static site + SPA, then serve with Caddy (which also proxies /api and /webhooks to the backend).
FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_BRAND=Saathi
ARG VITE_CONTACT_EMAIL=hello@saathi.example
ENV VITE_BRAND=$VITE_BRAND VITE_CONTACT_EMAIL=$VITE_CONTACT_EMAIL
RUN npm run build

FROM caddy:2-alpine
COPY --from=build /web/dist /srv
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY deploy/entrypoint.sh /entrypoint.sh
# a checkout on Windows may carry CRLF line endings, which this shell script and the Caddyfile must not have
RUN sed -i 's/\r$//' /entrypoint.sh /etc/caddy/Caddyfile
ENV API_BASE="" SITE_ADDRESS=":80" API_UPSTREAM="api:8000" INGRESS_UPSTREAM="ingress:8000"
EXPOSE 80 443
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
