"""Sign-in to the founder's console (ADR 0028). The product signs shop owners in with a WhatsApp code, which is right for
them and not enough here, so this is a separate path with a second factor:

  1. a WhatsApp code, to a platform operator who has an authenticator enrolled  -> a five-minute *ticket*
  2. an authenticator code (RFC 6238)                                          -> a short *session*

A ticket is not a session and opens nothing. Authenticator codes work once (a used step is remembered), five wrong ones lock the
account for fifteen minutes, and the account's secret is encrypted under a key only this part can derive. Tokens are signed with
`ADMIN_JWT_SECRET`, which the public application does not hold, so it can neither mint nor accept them. There is no refresh:
a session ends at its fixed lifetime, and is extended only by another fresh authenticator code. Enrolment is a command run on
the server, never a web page, so the first factor cannot be set up by whoever reaches the site."""
from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from salesai import crypto
from salesai.config import Settings
from salesai.db import Database
from salesai.modules.auth import totp
from salesai.modules.auth.service import AuthError, AuthService, Principal
from salesai.modules.tenants import platform_audit

log = logging.getLogger("salesai.admin")
AUDIENCE = "chatdeal-admin"
TICKET_S = 300
MAX_FAILURES = 5
LOCK = timedelta(minutes=15)
ISSUER = "ChatDeal Admin"


def _key(settings: Settings) -> bytes:
    if not settings.admin_jwt_secret:
        raise ValueError("ADMIN_JWT_SECRET is not set")
    return hashlib.sha256(b"chatdeal-admin-totp|" + settings.admin_jwt_secret.encode()).digest()


async def enroll_admin(db: Database, settings: Settings, phone: str) -> str:
    """Give a platform operator an authenticator (or replace theirs, clearing any lock). Returns the secret to import into the
    authenticator app: shown once, never stored in the clear. Only an existing operator account can be enrolled."""
    from salesai.phone import normalize_phone
    try:
        phone = normalize_phone(phone)
    except ValueError as e:
        raise ValueError(f"{phone!r} is not a valid phone number") from e
    secret = totp.new_secret()
    blob = crypto.encrypt(_key(settings), secret, aad=b"admin-totp")
    async with db.system_tx() as c:
        acct = await (await c.execute("SELECT id FROM accounts WHERE phone=%s AND platform_role='operator' AND status='active'", (phone,))).fetchone()
        if acct is None:
            raise ValueError("no active platform operator account has that number")
        await c.execute(
            """INSERT INTO admin_credentials (account_id, totp_secret_enc) VALUES (%s,%s)
               ON CONFLICT (account_id) DO UPDATE SET totp_secret_enc = EXCLUDED.totp_secret_enc, enrolled_at = now(), last_used_step = 0,
                                                      failed_attempts = 0, locked_until = NULL""", (acct["id"], blob))
        await c.execute("UPDATE admin_sessions SET revoked_at = now() WHERE account_id=%s AND revoked_at IS NULL", (acct["id"],))
        await platform_audit(c, actor_account_id=acct["id"], actor_label="admin", action="admin.enrolled", business_id=None)
    return secret


@dataclass
class AdminSession:
    access_token: str
    expires_in: int
    role: str = "operator"


class AdminAuth:
    def __init__(self, db: Database, settings: Settings, auth: AuthService, *, clock: Callable[[], float] = time.time):
        self.db, self.s, self.auth, self.clock = db, settings, auth, clock

    # ------------------------------------------------------------------ small helpers
    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), UTC)

    def _token(self, acct_id: uuid.UUID, sid: uuid.UUID, role: str, ttl: int) -> str:
        now = int(self.clock())
        return jwt.encode({"sub": str(acct_id), "sid": str(sid), "role": role, "aud": AUDIENCE, "typ": "admin", "iat": now, "exp": now + ttl},
                          self.s.admin_jwt_secret, algorithm="HS256")

    async def _eligible(self, phone: str) -> bool:
        """An active platform operator with an authenticator enrolled. Nobody else can even get a code."""
        if not self.s.admin_jwt_secret or self.s.otp_accept_any:
            return False
        async with self.db.system_tx() as c:
            return await (await c.execute(
                """SELECT 1 FROM accounts a JOIN admin_credentials k ON k.account_id = a.id
                   WHERE a.phone=%s AND a.platform_role='operator' AND a.status='active'""", (phone,))).fetchone() is not None

    async def _audit(self, action: str, account_id: uuid.UUID | None, detail: dict[str, Any] | None = None) -> None:
        async with self.db.system_tx() as c:
            await platform_audit(c, actor_account_id=account_id, actor_label="admin", action=action, business_id=None, detail=detail)

    # ------------------------------------------------------------------ step 1: the WhatsApp code
    async def request_code(self, raw_phone: str, ip: str | None) -> None:
        """Always looks the same from outside: a code is sent only to an enrolled operator, and nothing says whether one was."""
        from salesai.phone import normalize_phone
        try:
            phone = normalize_phone(raw_phone)
        except ValueError:
            return
        if not await self._eligible(phone):
            return
        try:
            await self.auth.request_otp(phone, ip)
        except AuthError as e:                      # rate limits and delivery failures must not reveal who is an operator either
            log.warning("admin code request not sent: %s", e.code)

    async def verify_code(self, raw_phone: str, code: str, ip: str | None) -> AdminSession:  # noqa: ARG002
        from salesai.phone import normalize_phone
        bad = AuthError("invalid_code", "That code is wrong or has expired.")
        try:
            phone = normalize_phone(raw_phone)
        except ValueError:
            raise bad from None
        if not await self._eligible(phone) or not await self.auth.consume_code(phone, code):
            await self._audit("admin.sign_in_failed", None, {"step": "whatsapp_code"})
            raise bad
        async with self.db.system_tx() as c:
            acct = await (await c.execute("SELECT id FROM accounts WHERE phone=%s", (phone,))).fetchone()
            assert acct is not None
            sid = uuid.uuid4()
            await c.execute("INSERT INTO admin_sessions (id, account_id, stage, ip, expires_at) VALUES (%s,%s,'ticket',%s,%s)",
                            (sid, acct["id"], ip, self._now() + timedelta(seconds=TICKET_S)))
        return AdminSession(self._token(acct["id"], sid, "admin_ticket", TICKET_S), TICKET_S, "admin_ticket")

    # ------------------------------------------------------------------ step 2: the authenticator code
    async def verify_totp(self, ticket: str, code: str, ip: str | None) -> AdminSession:
        claims, row = await self._load(ticket, "ticket")
        acct_id = uuid.UUID(claims["sub"])
        await self._check_authenticator(acct_id, code)
        async with self.db.system_tx() as c:
            await c.execute("UPDATE admin_sessions SET revoked_at = now() WHERE id=%s", (row["id"],))        # a ticket is used once
        session = await self._start_session(acct_id, ip)
        await self._audit("admin.signed_in", acct_id)
        return session

    async def _check_authenticator(self, acct_id: uuid.UUID, code: str) -> None:
        """Accept the code (once) or raise. Failures are counted and COMMITTED before raising: a raise inside the transaction
        would roll the counter back and make the lock pointless."""
        failure: AuthError | None = None
        locked_now = False
        async with self.db.system_tx() as c:
            k = await (await c.execute("SELECT * FROM admin_credentials WHERE account_id=%s FOR UPDATE", (acct_id,))).fetchone()
            now = self._now()
            if k is None:
                failure = AuthError("invalid_code", "That code is wrong or has expired.")
            elif k["locked_until"] is not None and k["locked_until"] > now:
                failure = AuthError("locked", "Too many wrong codes. Try again in a few minutes.", 429)
            else:
                secret = crypto.decrypt(_key(self.s), bytes(k["totp_secret_enc"]), aad=b"admin-totp")
                step = totp.verify(secret, code.strip(), now=self.clock(), after_step=int(k["last_used_step"]))
                if step is None:
                    n = k["failed_attempts"] + 1
                    locked_now = n >= MAX_FAILURES
                    await c.execute("UPDATE admin_credentials SET failed_attempts=%s, locked_until=%s WHERE account_id=%s",
                                    (0 if locked_now else n, now + LOCK if locked_now else None, acct_id))
                    failure = AuthError("invalid_code", "That code is wrong or has expired.")
                else:
                    await c.execute("UPDATE admin_credentials SET last_used_step=%s, failed_attempts=0, locked_until=NULL WHERE account_id=%s",
                                    (step, acct_id))
        if failure is not None:
            await self._audit("admin.locked" if locked_now else "admin.sign_in_failed", acct_id, {"step": "authenticator"})
            raise failure

    async def _start_session(self, acct_id: uuid.UUID, ip: str | None) -> AdminSession:
        ttl = self.s.admin_session_minutes * 60
        sid = uuid.uuid4()
        async with self.db.system_tx() as c:
            await c.execute("INSERT INTO admin_sessions (id, account_id, stage, ip, expires_at) VALUES (%s,%s,'session',%s,%s)",
                            (sid, acct_id, ip, self._now() + timedelta(seconds=ttl)))
        return AdminSession(self._token(acct_id, sid, "operator", ttl), ttl)

    # ------------------------------------------------------------------ using, extending and ending a session
    async def _load(self, token: str, stage: str) -> tuple[dict[str, Any], dict[str, Any]]:
        bad = AuthError("invalid_token", "Invalid credentials.")
        if not self.s.admin_jwt_secret:
            raise bad
        try:
            claims = jwt.decode(token, self.s.admin_jwt_secret, algorithms=["HS256"], audience=AUDIENCE,
                                options={"require": ["exp", "sub", "sid", "aud"], "verify_exp": False})
            sid = uuid.UUID(claims["sid"])
        except (jwt.PyJWTError, ValueError, KeyError):
            raise bad from None
        if claims.get("typ") != "admin" or claims.get("role") != ("operator" if stage == "session" else "admin_ticket"):
            raise bad
        if claims["exp"] <= self.clock():
            raise AuthError("token_expired", "Your session expired.")
        async with self.db.system_tx() as c:
            row = await (await c.execute("SELECT * FROM admin_sessions WHERE id=%s", (sid,))).fetchone()
        if row is None or row["stage"] != stage or row["revoked_at"] is not None or row["expires_at"] <= self._now():
            raise AuthError("session_revoked", "This session was signed out.")
        return claims, row

    async def authenticate(self, token: str) -> Principal:
        claims, row = await self._load(token, "session")
        async with self.db.system_tx() as c:          # still an operator, still enrolled: losing either ends the session at once
            acct = await (await c.execute(
                """SELECT a.phone FROM accounts a JOIN admin_credentials k ON k.account_id = a.id
                   WHERE a.id=%s AND a.platform_role='operator' AND a.status='active'""", (uuid.UUID(claims["sub"]),))).fetchone()
        if acct is None:
            raise AuthError("invalid_token", "Invalid credentials.")
        return Principal(uuid.UUID(claims["sub"]), row["id"], None, "operator", acct["phone"])

    async def extend(self, principal: Principal, code: str, ip: str | None) -> AdminSession:
        await self._check_authenticator(principal.account_id, code)
        async with self.db.system_tx() as c:
            await c.execute("UPDATE admin_sessions SET revoked_at = now() WHERE id=%s", (principal.session_id,))
        session = await self._start_session(principal.account_id, ip)
        await self._audit("admin.session_extended", principal.account_id)
        return session

    async def logout(self, principal: Principal) -> None:
        async with self.db.system_tx() as c:
            await c.execute("UPDATE admin_sessions SET revoked_at = now() WHERE account_id=%s AND revoked_at IS NULL", (principal.account_id,))
        await self._audit("admin.signed_out", principal.account_id)

    async def expires_at(self, principal: Principal) -> datetime:
        async with self.db.system_tx() as c:
            row = await (await c.execute("SELECT expires_at FROM admin_sessions WHERE id=%s", (principal.session_id,))).fetchone()
        assert row is not None
        expires: datetime = row["expires_at"]
        return expires
