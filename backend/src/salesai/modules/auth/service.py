"""Number-centric authentication. The WhatsApp number IS the identity.

  * one-time codes delivered to the number through the messaging channel (WhatsApp authentication template;
    the simulator in development) — the code is stored only as an HMAC, expires in minutes, has an attempt
    limit and is single-use
  * per-number and per-IP rate limits; the response never reveals whether a number is registered
  * short-lived access JWT + rotating refresh token; presenting a refresh token twice (theft) revokes the
    whole token family
  * sessions are server-side rows: a device can be revoked and the access token stops working immediately
Tenant context is derived from the session's business, never from anything the client sends (HLD Security)."""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from salesai import crypto
from salesai.config import Settings
from salesai.db import Database
from salesai.modules.channels import ChannelRegistry
from salesai.phone import normalize_phone, wa_id

log = logging.getLogger("salesai.auth")


class AuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 401):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


@dataclass
class Principal:
    account_id: uuid.UUID
    session_id: uuid.UUID
    business_id: uuid.UUID | None
    role: str                      # owner | staff | operator
    phone: str
    impersonated_by: uuid.UUID | None = None


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    business_id: uuid.UUID | None
    role: str


class AuthService:
    def __init__(self, db: Database, channels: ChannelRegistry, settings: Settings):
        self.db, self.channels, self.s = db, channels, settings
        self._revoked_cache: dict[uuid.UUID, tuple[float, bool]] = {}

    # ------------------------------------------------------------------ one-time codes
    def _hash(self, phone: str, code: str) -> str:
        return crypto.hmac_hex(self.s.otp_secret, phone, code)

    async def request_otp(self, raw_phone: str, ip: str | None) -> dict[str, Any]:
        try:
            phone = normalize_phone(raw_phone)
        except ValueError as e:
            raise AuthError("invalid_phone", "Enter a valid mobile number with country code.", 422) from e
        async with self.db.system_tx() as c:
            n_phone = (await (await c.execute(
                "SELECT count(*) AS n FROM otp_challenges WHERE phone=%s AND created_at > now() - interval '1 hour'", (phone,))).fetchone())["n"]
            n_ip = (await (await c.execute(
                "SELECT count(*) AS n FROM otp_challenges WHERE ip=%s AND created_at > now() - interval '1 hour'", (ip,))).fetchone())["n"] if ip else 0
            if n_phone >= self.s.otp_per_phone_per_hour or n_ip >= self.s.otp_per_ip_per_hour:
                raise AuthError("rate_limited", "Too many code requests. Please try again later.", 429)
            recent = await (await c.execute(
                "SELECT created_at FROM otp_challenges WHERE phone=%s ORDER BY created_at DESC LIMIT 1", (phone,))).fetchone()
            if recent and (datetime.now(UTC) - recent["created_at"]).total_seconds() < self.s.otp_min_interval_s:
                raise AuthError("too_soon", "Please wait a few seconds before requesting another code.", 429)
            acct = await (await c.execute("SELECT id, language, status FROM accounts WHERE phone=%s", (phone,))).fetchone()
            code = crypto.numeric_code(6)
            await c.execute(
                "INSERT INTO otp_challenges (phone, code_hash, expires_at, ip) VALUES (%s,%s, now() + make_interval(secs => %s), %s)",
                (phone, self._hash(phone, code), self.s.otp_ttl_s, ip))
        # Unknown / blocked numbers get the identical response and no message: no account enumeration.
        if acct and acct["status"] == "active":
            ref, channel = self.channels.platform_sender()
            res = await channel.send_template(ref, wa_id(phone), "otp_login", acct["language"], [code])
            if not res.ok:
                log.error("otp delivery failed: %s", res.error)
                raise AuthError("delivery_failed", "We couldn't send the code to WhatsApp. Please try again.", 502)
        return {"status": "sent", "expires_in": self.s.otp_ttl_s}

    async def verify_otp(self, raw_phone: str, code: str, ip: str | None, device: str | None) -> TokenPair | dict[str, Any]:
        try:
            phone = normalize_phone(raw_phone)
        except ValueError as e:
            raise AuthError("invalid_phone", "Enter a valid mobile number with country code.", 422) from e
        async with self.db.system_tx() as c:
            ch = await (await c.execute(
                """SELECT id, code_hash, attempts FROM otp_challenges
                   WHERE phone=%s AND consumed_at IS NULL AND expires_at > now() ORDER BY created_at DESC LIMIT 1 FOR UPDATE""", (phone,))).fetchone()
            if ch is None:
                raise AuthError("invalid_code", "That code is wrong or has expired.")
            if ch["attempts"] >= self.s.otp_max_attempts:
                raise AuthError("locked", "Too many wrong attempts. Request a new code.", 429)
            wrong = not crypto.constant_time_equal(ch["code_hash"], self._hash(phone, code.strip()))
            acct = None
            memberships: list[dict[str, Any]] = []
            if wrong:        # count the failure and COMMIT it before raising (a raise inside the tx would roll it back)
                await c.execute("UPDATE otp_challenges SET attempts = attempts + 1 WHERE id=%s", (ch["id"],))
            else:
                await c.execute("UPDATE otp_challenges SET consumed_at = now() WHERE id=%s", (ch["id"],))
                acct = await (await c.execute("SELECT * FROM accounts WHERE phone=%s AND status='active'", (phone,))).fetchone()
                if acct is not None:
                    await c.execute("UPDATE accounts SET last_login_at = now() WHERE id=%s", (acct["id"],))
                    memberships = await (await c.execute(
                        """SELECT bu.business_id, bu.role, b.name FROM business_users bu JOIN businesses b ON b.id = bu.business_id
                           WHERE bu.account_id=%s AND b.status <> 'churned' ORDER BY b.name""", (acct["id"],))).fetchall()
        if wrong or acct is None:
            raise AuthError("invalid_code", "That code is wrong or has expired.")
        if acct["platform_role"] == "operator":
            return await self._issue(acct, None, "operator", device, ip)
        if not memberships:
            raise AuthError("no_business", "This number isn't linked to a business yet. Ask your operator to add it.", 403)
        if len(memberships) == 1:
            return await self._issue(acct, memberships[0]["business_id"], memberships[0]["role"], device, ip)
        # several businesses on one number: a short-lived, business-less ticket lets the user choose
        ticket = await self._issue(acct, None, "pending", device, ip, ttl=600)
        return {"choose_business": True, "businesses": [{"id": str(m["business_id"]), "name": m["name"], "role": m["role"]} for m in memberships],
                "ticket": ticket.access_token}

    # ------------------------------------------------------------------ tokens / sessions
    def _access(self, acct_id: uuid.UUID, sid: uuid.UUID, bid: uuid.UUID | None, role: str, ttl: int, imp: uuid.UUID | None = None) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {"sub": str(acct_id), "sid": str(sid), "bid": str(bid) if bid else None, "role": role,
                                  "iat": now, "exp": now + ttl, "typ": "access"}
        if imp:
            claims["imp"] = str(imp)
        return jwt.encode(claims, self.s.jwt_secret, algorithm="HS256")

    async def _issue(self, acct: dict[str, Any], bid: uuid.UUID | None, role: str, device: str | None, ip: str | None,
                     *, family: uuid.UUID | None = None, ttl: int | None = None, imp: uuid.UUID | None = None) -> TokenPair:
        refresh = crypto.random_token(32)
        sid, fam = uuid.uuid4(), family or uuid.uuid4()
        ttl = ttl or self.s.access_token_ttl_s
        async with self.db.system_tx() as c:
            await c.execute(
                """INSERT INTO auth_sessions (id, account_id, business_id, family_id, refresh_hash, device_label, ip, expires_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s, now() + make_interval(secs => %s))""",
                (sid, acct["id"], bid, fam, crypto.sha256_hex(refresh), (device or "")[:120] or None, ip, self.s.refresh_token_ttl_s))
        return TokenPair(self._access(acct["id"], sid, bid, role, ttl, imp), refresh, ttl, bid, role)

    async def refresh(self, refresh_token: str, ip: str | None) -> TokenPair:
        h = crypto.sha256_hex(refresh_token)
        failure: AuthError | None = None
        new: TokenPair | None = None
        async with self.db.system_tx() as c:
            s = await (await c.execute("SELECT * FROM auth_sessions WHERE refresh_hash=%s FOR UPDATE", (h,))).fetchone()
            if s is None:
                failure = AuthError("invalid_refresh", "Please sign in again.")
            elif s["revoked_at"] is not None:
                # a rotated/revoked token was presented again: assume theft, kill the whole family (committed below)
                if s["replaced_by"] is not None:
                    await c.execute("UPDATE auth_sessions SET revoked_at = COALESCE(revoked_at, now()) WHERE family_id=%s", (s["family_id"],))
                    log.warning("refresh token reuse detected; family revoked")
                failure = AuthError("invalid_refresh", "Please sign in again.")
            elif s["expires_at"] <= datetime.now(UTC):
                failure = AuthError("invalid_refresh", "Your session expired. Please sign in again.")
            else:
                acct = await (await c.execute("SELECT * FROM accounts WHERE id=%s AND status='active'", (s["account_id"],))).fetchone()
                role = await self._role(c, acct, s["business_id"]) if acct else None
                if acct is None or role is None:
                    failure = AuthError("invalid_refresh", "Your access has changed. Please sign in again.")
                else:
                    new = await self._rotate(c, s, acct, role, ip)
        self._revoked_cache.clear()
        if failure is not None or new is None:
            raise failure or AuthError("invalid_refresh", "Please sign in again.")
        return new

    async def _role(self, c, acct: dict[str, Any], bid: uuid.UUID | None) -> str | None:  # noqa: ANN001
        if bid is None:
            return "operator" if acct["platform_role"] == "operator" else None
        r = await (await c.execute("SELECT role FROM business_users WHERE account_id=%s AND business_id=%s", (acct["id"], bid))).fetchone()
        return r["role"] if r else None

    async def _rotate(self, c, s: dict[str, Any], acct: dict[str, Any], role: str, ip: str | None) -> TokenPair:  # noqa: ANN001
        refresh = crypto.random_token(32)
        sid = uuid.uuid4()
        await c.execute(
            """INSERT INTO auth_sessions (id, account_id, business_id, family_id, refresh_hash, device_label, ip, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (sid, acct["id"], s["business_id"], s["family_id"], crypto.sha256_hex(refresh), s["device_label"], ip, s["expires_at"]))
        await c.execute("UPDATE auth_sessions SET revoked_at = now(), replaced_by = %s WHERE id=%s", (sid, s["id"]))
        return TokenPair(self._access(acct["id"], sid, s["business_id"], role, self.s.access_token_ttl_s), refresh, self.s.access_token_ttl_s, s["business_id"], role)

    async def select_business(self, ticket: str, business_id: uuid.UUID, ip: str | None) -> TokenPair:
        p = await self.authenticate(ticket, allow_pending=True)
        if p.role != "pending":
            raise AuthError("forbidden", "Invalid ticket.", 403)
        async with self.db.system_tx() as c:
            acct = await (await c.execute("SELECT * FROM accounts WHERE id=%s", (p.account_id,))).fetchone()
            role = await self._role(c, acct, business_id)
            if role is None:
                raise AuthError("forbidden", "You don't have access to that business.", 403)
            await c.execute("UPDATE auth_sessions SET revoked_at = now() WHERE id=%s", (p.session_id,))
        return await self._issue(acct, business_id, role, "browser", ip)

    async def switch_business(self, principal: Principal, business_id: uuid.UUID, ip: str | None) -> TokenPair:
        async with self.db.system_tx() as c:
            acct = await (await c.execute("SELECT * FROM accounts WHERE id=%s", (principal.account_id,))).fetchone()
            role = await self._role(c, acct, business_id)
            if role is None:
                raise AuthError("forbidden", "You don't have access to that business.", 403)
        return await self._issue(acct, business_id, role, "browser", ip)

    async def impersonate(self, operator: Principal, business_id: uuid.UUID, ip: str | None) -> TokenPair:
        """Operator support access: a short-lived owner-role token for one business, audited."""
        async with self.db.system_tx() as c:
            acct = await (await c.execute("SELECT * FROM accounts WHERE id=%s", (operator.account_id,))).fetchone()
            ok = await (await c.execute("SELECT 1 FROM businesses WHERE id=%s", (business_id,))).fetchone()
        if not ok:
            raise AuthError("not_found", "Business not found.", 404)
        pair = await self._issue(acct, business_id, "owner", "operator-impersonation", ip, ttl=1800, imp=operator.account_id)
        async with self.db.tenant(business_id) as c:
            await c.execute("INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity) VALUES (%s,%s,'operator','operator.impersonation','business')",
                            (business_id, operator.account_id))
        return pair

    async def authenticate(self, token: str, *, allow_pending: bool = False) -> Principal:
        try:
            claims = jwt.decode(token, self.s.jwt_secret, algorithms=["HS256"], options={"require": ["exp", "sub", "sid"]})
        except jwt.ExpiredSignatureError as e:
            raise AuthError("token_expired", "Your session expired.") from e
        except jwt.PyJWTError as e:
            raise AuthError("invalid_token", "Invalid credentials.") from e
        if claims.get("typ") != "access":
            raise AuthError("invalid_token", "Invalid credentials.")
        sid = uuid.UUID(claims["sid"])
        if await self._is_revoked(sid):
            raise AuthError("session_revoked", "This session was signed out.")
        role = claims["role"]
        if role == "pending" and not allow_pending:
            raise AuthError("forbidden", "Choose a business first.", 403)
        acct = await self._account_phone(uuid.UUID(claims["sub"]))
        return Principal(uuid.UUID(claims["sub"]), sid, uuid.UUID(claims["bid"]) if claims.get("bid") else None, role, acct,
                         uuid.UUID(claims["imp"]) if claims.get("imp") else None)

    async def _is_revoked(self, sid: uuid.UUID) -> bool:
        now = time.monotonic()
        hit = self._revoked_cache.get(sid)
        if hit and now - hit[0] < 5:
            return hit[1]
        async with self.db.system_tx() as c:
            r = await (await c.execute("SELECT revoked_at, expires_at FROM auth_sessions WHERE id=%s", (sid,))).fetchone()
        revoked = r is None or r["revoked_at"] is not None or r["expires_at"] <= datetime.now(UTC)
        # a rotated session id is revoked by design; only the access token's own session id is checked here, and
        # an access token outlives its refresh rotation by at most its short TTL.
        self._revoked_cache[sid] = (now, revoked)
        return revoked

    async def _account_phone(self, account_id: uuid.UUID) -> str:
        async with self.db.system_tx() as c:
            r = await (await c.execute("SELECT phone, status FROM accounts WHERE id=%s", (account_id,))).fetchone()
        if r is None or r["status"] != "active":
            raise AuthError("invalid_token", "Invalid credentials.")
        return r["phone"]

    async def logout(self, principal: Principal) -> None:
        async with self.db.system_tx() as c:
            await c.execute("UPDATE auth_sessions SET revoked_at = now() WHERE family_id = (SELECT family_id FROM auth_sessions WHERE id=%s)", (principal.session_id,))
        self._revoked_cache.pop(principal.session_id, None)

    async def sessions(self, principal: Principal) -> list[dict[str, Any]]:
        async with self.db.system_tx() as c:
            rows = await (await c.execute(
                """SELECT DISTINCT ON (family_id) id, family_id, device_label, ip, created_at, last_used_at FROM auth_sessions
                   WHERE account_id=%s AND revoked_at IS NULL AND expires_at > now() ORDER BY family_id, created_at DESC""", (principal.account_id,))).fetchall()
        cur = None
        async with self.db.system_tx() as c:
            r = await (await c.execute("SELECT family_id FROM auth_sessions WHERE id=%s", (principal.session_id,))).fetchone()
            cur = r["family_id"] if r else None
        return [{"id": str(x["family_id"]), "device": x["device_label"], "ip": x["ip"], "signed_in_at": x["created_at"], "current": x["family_id"] == cur} for x in rows]

    async def revoke_family(self, principal: Principal, family_id: uuid.UUID) -> bool:
        async with self.db.system_tx() as c:
            r = await c.execute("UPDATE auth_sessions SET revoked_at = now() WHERE family_id=%s AND account_id=%s AND revoked_at IS NULL", (family_id, principal.account_id))
        self._revoked_cache.clear()
        return r.rowcount > 0


def expiry(seconds: int) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)
