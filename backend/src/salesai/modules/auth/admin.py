"""Sign-in to the founder's console (ADR 0028; the sign-in flow is ADR 0047; session records are ADR 0048). The product signs shop owners in with a
WhatsApp code, which is right for them and not enough here, so this is a separate path:

  * There is exactly one founder, and the founder is a deployment setting (`FOUNDER_PHONE`), not a database fact. Every request is checked against
    that setting; no API, screen or database value can add, change or replace the founder.
  * The first proof is an authenticator code (RFC 6238). Nothing else is asked, so there is no number to type and no WhatsApp message to trigger.
  * A correct code opens a session, unless the sign-in looks suspicious (an unknown browser, wrong codes since the last good sign-in, a new network,
    a lock, the first sign-in after enrolment). Then it returns a five-minute *ticket* and a WhatsApp code, sent to the founder's number only after
    the authenticator passed, is the second layer. A ticket is not a session and opens nothing.
  * Every session records where it came from, on what, and how it was proven; every refused attempt is on the audit trail with the same details. The
    founder can see them and end any session, and ending someone else's session needs a fresh authenticator code, so a stolen token alone cannot.

Authenticator codes work once (a used step is remembered), five wrong codes of either kind lock the account for fifteen minutes, and the secret is
encrypted under a key only this part can derive. Tokens are signed with `ADMIN_JWT_SECRET`, which the public application does not hold, so it can
neither mint nor accept them. There is no refresh: a session ends at its fixed lifetime and is extended only by another fresh authenticator code.
Enrolment is a command run on the server (it prints a QR code), never a web page, so the first factor cannot be set up by whoever reaches the site."""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from salesai import crypto
from salesai.alerts import raise_alert
from salesai.config import Settings
from salesai.db import Database, jsonb
from salesai.modules.auth import totp
from salesai.modules.auth.device import (  # noqa: F401
    ClientInfo,
    clean_geo,
    describe_device,
    network_of,
    where_label,
)
from salesai.modules.auth.service import AuthError, AuthService, Principal
from salesai.modules.tenants import platform_audit

log = logging.getLogger("salesai.admin")
AUDIENCE = "chatdeal-admin"
TICKET_S = 300
MAX_FAILURES = 5          # wrong codes (of either kind) in a row that lock the account
WARN_AFTER = 3            # wrong codes since the last good sign-in that tell the founder
LOCK = timedelta(minutes=15)
MAX_DEVICES = 5           # browsers that can be trusted at once
SEEN_EVERY_S = 60         # a session's "last used" is written at most this often
ISSUER = "ChatDeal Admin"
WRONG = "That code is wrong or has expired."


def _key(settings: Settings) -> bytes:
    if not settings.admin_jwt_secret:
        raise ValueError("ADMIN_JWT_SECRET is not set")
    return hashlib.sha256(b"chatdeal-admin-totp|" + settings.admin_jwt_secret.encode()).digest()


def _device_hash(token: str) -> str:
    return hashlib.sha256(b"chatdeal-admin-device|" + token.encode()).hexdigest()


def _ctx(info: ClientInfo) -> dict[str, Any]:
    """What goes on the audit trail about where a request came from."""
    return {"ip": info.ip, "device": describe_device(info.user_agent), "where": where_label(info.ip, info.geo), "user_agent": (info.user_agent or "")[:300]}


async def enroll_admin(db: Database, settings: Settings) -> str:
    """Set up (or replace) the one founder's authenticator. The founder is `FOUNDER_PHONE`; the account is created if it does not exist, any
    other operator is demoted and loses their authenticator, every console session ends and every trusted browser is forgotten. Returns the secret
    to import into the authenticator app: shown once, never stored in the clear."""
    phone = settings.founder_phone
    if not phone:
        raise ValueError("FOUNDER_PHONE is not set: the founder's number is a deployment setting (ADR 0047)")
    secret = totp.new_secret()
    blob = crypto.encrypt(_key(settings), secret, aad=b"admin-totp")
    async with db.console_tx() as c:
        acct = await (await c.execute(
            """INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Founder','operator')
               ON CONFLICT (phone) DO UPDATE SET platform_role = 'operator' RETURNING id, status""", (phone,))).fetchone()
        assert acct is not None
        if acct["status"] != "active":
            raise ValueError("the founder's account is blocked; unblock it in the database first")
        await c.execute("UPDATE accounts SET platform_role = NULL WHERE platform_role = 'operator' AND id <> %s", (acct["id"],))
        await c.execute("DELETE FROM admin_credentials WHERE account_id <> %s", (acct["id"],))
        await c.execute(
            """INSERT INTO admin_credentials (account_id, totp_secret_enc) VALUES (%s,%s)
               ON CONFLICT (account_id) DO UPDATE SET totp_secret_enc = EXCLUDED.totp_secret_enc, enrolled_at = now(), last_used_step = 0,
                   failed_attempts = 0, locked_until = NULL, failed_since_login = 0, last_failed_at = NULL, last_login_at = NULL,
                   last_login_ip = NULL, was_locked = false, trusted_devices = '[]'""", (acct["id"], blob))
        await c.execute("UPDATE admin_sessions SET revoked_at = now(), ended_reason = 'enrolment' WHERE revoked_at IS NULL")
        await platform_audit(c, actor_account_id=acct["id"], actor_label="admin", action="admin.enrolled", business_id=None)
    return secret


@dataclass
class AdminSession:
    access_token: str
    expires_in: int
    role: str = "operator"


@dataclass
class SignIn:
    """What a sign-in attempt came to: a session, or (when it looked suspicious) a ticket that needs the WhatsApp code too."""
    session: AdminSession | None = None
    ticket: AdminSession | None = None
    reason: str | None = None          # why the second layer is needed, in the founder's terms (shown to the founder after the authenticator passed)
    device_token: str | None = None    # the trusted-browser token to keep in a cookie after a full sign-in


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

    async def _founder(self) -> dict[str, Any] | None:
        """The one founder: the account whose number is FOUNDER_PHONE, an active operator with an authenticator enrolled. Nobody else exists here."""
        if not self.s.admin_jwt_secret or not self.s.founder_phone or self.s.otp_accept_any:
            return None
        async with self.db.console_tx() as c:
            row = await (await c.execute(
                """SELECT a.id, a.phone FROM accounts a JOIN admin_credentials k ON k.account_id = a.id
                   WHERE a.phone=%s AND a.platform_role='operator' AND a.status='active'""", (self.s.founder_phone,))).fetchone()
        return dict(row) if row else None

    async def _audit(self, action: str, account_id: uuid.UUID | None, detail: dict[str, Any] | None = None) -> None:
        async with self.db.console_tx() as c:
            await platform_audit(c, actor_account_id=account_id, actor_label="admin", action=action, business_id=None, detail=detail)

    async def _record_failure(self, c: Any, k: dict[str, Any], now: datetime) -> tuple[bool, int]:
        """Count one wrong code on the credentials row `k` (already locked FOR UPDATE). Returns (this one locked the account, wrong codes since the last good sign-in)."""
        n = k["failed_attempts"] + 1
        locked_now = n >= MAX_FAILURES
        since = k["failed_since_login"] + 1
        await c.execute(
            """UPDATE admin_credentials SET failed_attempts=%s, failed_since_login=%s, last_failed_at=%s, locked_until=%s, was_locked = was_locked OR %s
               WHERE account_id=%s""", (0 if locked_now else n, since, now, now + LOCK if locked_now else None, locked_now, k["account_id"]))
        return locked_now, since

    async def _after_failure(self, acct_id: uuid.UUID, step: str, locked_now: bool, since: int, info: ClientInfo) -> None:
        """Audit (with where it came from), and tell the founder when it looks like someone guessing. Runs after the counter is committed."""
        ctx = _ctx(info)
        await self._audit("admin.locked" if locked_now else "admin.sign_in_failed", acct_id, {"step": step, **ctx})
        if locked_now:
            await self._alert("critical", "admin_locked", f"The console was locked after repeated wrong sign-in codes (last from {ctx['where']}, {ctx['device']})", {"step": step, **ctx}, "founder")
        elif since >= WARN_AFTER:
            await self._alert("high", "admin_wrong_codes", f"{since} wrong sign-in codes at the console since the last good sign-in (latest from {ctx['where']}, {ctx['device']})",
                              {"step": step, "wrong_codes": since, **ctx}, "founder")

    async def _alert(self, severity: str, kind: str, message: str, detail: dict[str, Any], dedup_key: str) -> None:
        try:
            async with self.db.console_tx() as c:
                await raise_alert(c, severity, kind, message, dedup_key=dedup_key, detail=detail)
        except Exception:  # noqa: BLE001 - a failing alert must never turn a refused sign-in into a crash
            log.exception("could not raise the console sign-in alert")

    # ------------------------------------------------------------------ the sign-in: authenticator first
    async def sign_in(self, code: str, info: ClientInfo, device: str | None) -> SignIn:
        founder = await self._founder()
        if founder is None:
            await self._audit("admin.sign_in_failed", None, {"step": "authenticator", "founder": "not enrolled", **_ctx(info)})
            raise AuthError("invalid_code", WRONG)
        await self._check_authenticator(founder["id"], code, info)
        reasons = await self._suspicion(founder["id"], info.ip, device)
        if reasons:
            ticket = await self._ticket(founder["id"], info)
            await self._audit("admin.step_up_required", founder["id"], {"reasons": reasons, **_ctx(info)})
            return SignIn(ticket=ticket, reason=reasons[0])
        return await self._complete(founder["id"], info, device, method="authenticator", reason=None)

    async def _suspicion(self, acct_id: uuid.UUID, ip: str | None, device: str | None) -> list[str]:
        """Why this sign-in needs the WhatsApp code too, most important first. Empty = routine."""
        async with self.db.console_tx() as c:
            k = await (await c.execute("SELECT * FROM admin_credentials WHERE account_id=%s", (acct_id,))).fetchone()
        assert k is not None
        reasons: list[str] = []
        if k["last_login_at"] is None:
            reasons.append("first_sign_in")
        elif not self._trusted(k["trusted_devices"], device):
            reasons.append("new_device")
        if k["failed_since_login"] > 0:
            reasons.append("wrong_codes")
        if k["was_locked"]:
            reasons.append("locked")
        if k["last_login_ip"] is not None and network_of(ip) != network_of(k["last_login_ip"]):
            reasons.append("new_network")
        return reasons

    def _trusted(self, devices: list[dict[str, str]], token: str | None) -> bool:
        if not token:
            return False
        h = _device_hash(token)
        oldest = self._now() - timedelta(days=self.s.admin_device_trust_days)
        return any(d["h"] == h and datetime.fromisoformat(d["at"]) > oldest for d in devices)

    async def _ticket(self, acct_id: uuid.UUID, info: ClientInfo) -> AdminSession:
        sid = uuid.uuid4()
        async with self.db.console_tx() as c:
            await c.execute("INSERT INTO admin_sessions (id, account_id, stage, ip, expires_at) VALUES (%s,%s,'ticket',%s,%s)",
                            (sid, acct_id, info.ip, self._now() + timedelta(seconds=TICKET_S)))
        return AdminSession(self._token(acct_id, sid, "admin_ticket", TICKET_S), TICKET_S, "admin_ticket")

    async def _complete(self, acct_id: uuid.UUID, info: ClientInfo, device: str | None, *, method: str, reason: str | None) -> SignIn:
        """A fully proven sign-in: start the session (recording where it came from), reset the suspicion counters and trust this browser
        (a new token, or the same one renewed). A sign-in that needed the second layer tells the founder."""
        now = self._now()
        oldest = now - timedelta(days=self.s.admin_device_trust_days)
        async with self.db.console_tx() as c:
            k = await (await c.execute("SELECT trusted_devices, last_login_ip FROM admin_credentials WHERE account_id=%s FOR UPDATE", (acct_id,))).fetchone()
            assert k is not None
            before = network_of(k["last_login_ip"])         # judged before this sign-in becomes the "last" one
            usual_network = None if before is None else before == network_of(info.ip)
            devices = [d for d in k["trusted_devices"] if datetime.fromisoformat(d["at"]) > oldest]
            known = next((d for d in devices if device and d["h"] == _device_hash(device)), None)
            if known is not None:
                known["at"] = now.isoformat()
                token = device
            else:
                token = secrets.token_urlsafe(32)
                devices.append({"h": _device_hash(token), "at": now.isoformat()})
                devices = sorted(devices, key=lambda d: d["at"])[-MAX_DEVICES:]
            assert token is not None
            await c.execute(
                """UPDATE admin_credentials SET failed_since_login = 0, was_locked = false, last_login_at = %s, last_login_ip = %s, trusted_devices = %s
                   WHERE account_id=%s""", (now, info.ip, jsonb(devices), acct_id))
        session, sid = await self._start_session(acct_id, info, _device_hash(token)[:16], method, reason, usual_network)
        ctx = _ctx(info)
        await self._audit("admin.signed_in", acct_id, {"new_browser": known is None, "method": method, "reason": reason, "session": str(sid), **ctx})
        if reason is not None and reason != "first_sign_in":
            await self._alert("high", "admin_new_sign_in", f"Console sign-in that needed the WhatsApp code ({reason.replace('_', ' ')}): {ctx['device']}, {ctx['where']}",
                              {"reason": reason, "session": str(sid), **ctx}, str(sid))
        return SignIn(session=session, device_token=token)

    # ------------------------------------------------------------------ the second layer: a WhatsApp code to the founder's number
    async def send_step_up_code(self, ticket: str, info: ClientInfo) -> None:
        """Send the WhatsApp code to the founder's number. Needs a ticket, which only a correct authenticator code can obtain, so a wrong guess
        can never make the product message the founder."""
        claims, _ = await self._load(ticket, "ticket")
        founder = await self._founder()
        if founder is None or str(founder["id"]) != claims["sub"]:
            raise AuthError("invalid_token", "Invalid credentials.")
        await self.auth.request_otp(founder["phone"], info.ip)
        await self._audit("admin.step_up_code_sent", founder["id"], _ctx(info))

    async def verify_step_up(self, ticket: str, code: str, info: ClientInfo, device: str | None) -> SignIn:
        claims, row = await self._load(ticket, "ticket")
        acct_id = uuid.UUID(claims["sub"])
        founder = await self._founder()
        if founder is None or founder["id"] != acct_id:
            raise AuthError("invalid_token", "Invalid credentials.")
        await self._refuse_if_locked(acct_id)
        if not await self.auth.consume_code(founder["phone"], code):
            await self._fail(acct_id, "whatsapp_code", info)
            raise AuthError("invalid_code", WRONG)
        async with self.db.console_tx() as c:
            await c.execute("UPDATE admin_sessions SET revoked_at = now() WHERE id=%s", (row["id"],))        # a ticket is used once
            reasons = await (await c.execute("SELECT detail FROM platform_audit_log WHERE action='admin.step_up_required' AND actor_account_id=%s ORDER BY id DESC LIMIT 1", (acct_id,))).fetchone()
        why = ((reasons or {}).get("detail") or {}).get("reasons") or ["new_device"]
        return await self._complete(acct_id, info, device, method="authenticator+whatsapp", reason=why[0])

    async def _refuse_if_locked(self, acct_id: uuid.UUID) -> None:
        async with self.db.console_tx() as c:
            k = await (await c.execute("SELECT locked_until FROM admin_credentials WHERE account_id=%s", (acct_id,))).fetchone()
        if k is not None and k["locked_until"] is not None and k["locked_until"] > self._now():
            raise AuthError("locked", "Too many wrong codes. Try again in a few minutes.", 429)

    async def _fail(self, acct_id: uuid.UUID, step: str, info: ClientInfo) -> None:
        """One wrong code of the second layer: it counts toward the same lock as the authenticator's."""
        async with self.db.console_tx() as c:
            k = await (await c.execute("SELECT * FROM admin_credentials WHERE account_id=%s FOR UPDATE", (acct_id,))).fetchone()
            if k is None:
                return
            locked_now, since = await self._record_failure(c, dict(k), self._now())
        await self._after_failure(acct_id, step, locked_now, since, info)

    # ------------------------------------------------------------------ the authenticator code
    async def _check_authenticator(self, acct_id: uuid.UUID, code: str, info: ClientInfo) -> None:
        """Accept the code (once) or raise. Failures are counted and COMMITTED before raising: a raise inside the transaction
        would roll the counter back and make the lock pointless."""
        failure: AuthError | None = None
        locked_now, since = False, 0
        async with self.db.console_tx() as c:
            k = await (await c.execute("SELECT * FROM admin_credentials WHERE account_id=%s FOR UPDATE", (acct_id,))).fetchone()
            now = self._now()
            if k is None:
                failure = AuthError("invalid_code", WRONG)
            elif k["locked_until"] is not None and k["locked_until"] > now:
                failure = AuthError("locked", "Too many wrong codes. Try again in a few minutes.", 429)
            else:
                secret = crypto.decrypt(_key(self.s), bytes(k["totp_secret_enc"]), aad=b"admin-totp")
                step = totp.verify(secret, code.strip(), now=self.clock(), after_step=int(k["last_used_step"]))
                if step is None:
                    locked_now, since = await self._record_failure(c, dict(k), now)
                    failure = AuthError("invalid_code", WRONG)
                else:
                    await c.execute("UPDATE admin_credentials SET last_used_step=%s, failed_attempts=0, locked_until=NULL WHERE account_id=%s", (step, acct_id))
        if failure is not None:
            if failure.code == "locked":
                await self._audit("admin.sign_in_refused_locked", acct_id, _ctx(info))
            else:
                await self._after_failure(acct_id, "authenticator", locked_now, since, info)
            raise failure

    async def _start_session(self, acct_id: uuid.UUID, info: ClientInfo, device_id: str | None, method: str, reason: str | None,
                             usual_network: bool | None = None) -> tuple[AdminSession, uuid.UUID]:
        ttl = self.s.admin_session_minutes * 60
        sid = uuid.uuid4()
        now = self._now()
        async with self.db.console_tx() as c:
            await c.execute(
                """INSERT INTO admin_sessions (id, account_id, stage, ip, expires_at, user_agent, device_label, device_id, geo, network, method, step_up_reason, last_seen_at, usual_network)
                   VALUES (%s,%s,'session',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (sid, acct_id, info.ip, now + timedelta(seconds=ttl), (info.user_agent or "")[:300], describe_device(info.user_agent), device_id, info.geo,
                 network_of(info.ip), method, reason, now, usual_network))
        return AdminSession(self._token(acct_id, sid, "operator", ttl), ttl), sid

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
        async with self.db.console_tx() as c:
            row = await (await c.execute("SELECT * FROM admin_sessions WHERE id=%s", (sid,))).fetchone()
        if row is None or row["stage"] != stage or row["revoked_at"] is not None or row["expires_at"] <= self._now():
            raise AuthError("session_revoked", "This session was signed out.")
        return claims, row

    async def authenticate(self, token: str) -> Principal:
        claims, row = await self._load(token, "session")
        async with self.db.console_tx() as c:          # still the founder, still an operator, still enrolled: losing any of them ends the session at once
            acct = await (await c.execute(
                """SELECT a.phone FROM accounts a JOIN admin_credentials k ON k.account_id = a.id
                   WHERE a.id=%s AND a.platform_role='operator' AND a.status='active'""", (uuid.UUID(claims["sub"]),))).fetchone()
            seen = row.get("last_seen_at")
            if acct is not None and (seen is None or (self._now() - seen).total_seconds() >= SEEN_EVERY_S):     # "last used", at most once a minute
                await c.execute("UPDATE admin_sessions SET last_seen_at = %s WHERE id=%s", (self._now(), row["id"]))
        if acct is None or not self.s.founder_phone or acct["phone"] != self.s.founder_phone:
            raise AuthError("invalid_token", "Invalid credentials.")
        return Principal(uuid.UUID(claims["sub"]), row["id"], None, "operator", acct["phone"])

    async def extend(self, principal: Principal, code: str, info: ClientInfo) -> AdminSession:
        await self._check_authenticator(principal.account_id, code, info)
        async with self.db.console_tx() as c:
            old = await (await c.execute("SELECT * FROM admin_sessions WHERE id=%s", (principal.session_id,))).fetchone()
            await c.execute("UPDATE admin_sessions SET revoked_at = now(), ended_reason = 'extended' WHERE id=%s", (principal.session_id,))
        assert old is not None
        session, _ = await self._start_session(principal.account_id, ClientInfo(old["ip"], old["user_agent"], old["geo"]), old["device_id"], "extended", old["step_up_reason"], old["usual_network"])
        await self._audit("admin.session_extended", principal.account_id, _ctx(info))
        return session

    async def logout(self, principal: Principal) -> None:
        """End this session (only this one): signing yourself out cannot hurt, so it needs no code."""
        async with self.db.console_tx() as c:
            await c.execute("UPDATE admin_sessions SET revoked_at = now(), ended_reason = 'signed_out' WHERE id=%s AND revoked_at IS NULL", (principal.session_id,))
        await self._audit("admin.signed_out", principal.account_id, {"session": str(principal.session_id)})

    async def expires_at(self, principal: Principal) -> datetime:
        async with self.db.console_tx() as c:
            row = await (await c.execute("SELECT expires_at FROM admin_sessions WHERE id=%s", (principal.session_id,))).fetchone()
        assert row is not None
        expires: datetime = row["expires_at"]
        return expires

    # ------------------------------------------------------------------ the security screen: see every session and attempt, end any session (ADR 0048)
    async def list_sessions(self, principal: Principal, limit: int = 50) -> list[dict[str, Any]]:
        now = self._now()
        async with self.db.console_tx() as c:
            rows = await (await c.execute(
                "SELECT * FROM admin_sessions WHERE account_id=%s AND stage='session' ORDER BY created_at DESC LIMIT %s", (principal.account_id, limit))).fetchall()
        out = []
        for r in rows:
            ended = r["revoked_at"] or (r["expires_at"] if r["expires_at"] <= now else None)
            status = "active" if ended is None else ("expired" if r["revoked_at"] is None else "ended")
            reason = r["ended_reason"] or ("expired" if status == "expired" else None)
            end = ended or now
            out.append({
                "id": r["id"], "status": status, "is_current": r["id"] == principal.session_id, "device": r["device_label"] or "Unknown device",
                "user_agent": r["user_agent"], "browser_id": r["device_id"], "ip": r["ip"], "network": r["network"], "country": r["geo"],
                "where": where_label(r["ip"], r["geo"]), "usual_network": bool(r["usual_network"]),
                "method": r["method"], "step_up_reason": r["step_up_reason"], "started_at": r["created_at"], "last_seen_at": r["last_seen_at"],
                "ended_at": ended, "ended_reason": reason, "expires_at": r["expires_at"], "duration_s": max(int((end - r["created_at"]).total_seconds()), 0)})
        return out

    async def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Console sign-in activity from the audit trail, newest first: successes, refused attempts, locks, second-layer requests, session endings."""
        async with self.db.console_tx() as c:
            rows = await (await c.execute(
                "SELECT id, action, detail, created_at FROM platform_audit_log WHERE action LIKE 'admin.%%' ORDER BY id DESC LIMIT %s", (limit,))).fetchall()
        return [{"id": r["id"], "action": r["action"], "at": r["created_at"], "detail": r["detail"] or {}} for r in rows]

    async def revoke_session(self, principal: Principal, session_id: uuid.UUID, code: str, info: ClientInfo, *, forget_browser: bool = True) -> None:
        """End one session. Needs a fresh authenticator code, so a stolen token alone cannot end the founder's sessions or tidy up its own traces."""
        await self._check_authenticator(principal.account_id, code, info)
        async with self.db.console_tx() as c:
            row = await (await c.execute("SELECT * FROM admin_sessions WHERE id=%s AND account_id=%s AND stage='session'", (session_id, principal.account_id))).fetchone()
            if row is None:
                raise AuthError("not_found", "No such session.", 404)
            await c.execute("UPDATE admin_sessions SET revoked_at = now(), ended_reason = 'revoked' WHERE id=%s AND revoked_at IS NULL", (session_id,))
            forgotten = 0
            if forget_browser and row["device_id"]:
                forgotten = await self._forget(c, principal.account_id, keep=None, only=row["device_id"])
        await self._audit("admin.session_revoked", principal.account_id, {"session": str(session_id), "device": row["device_label"], "ip": row["ip"], "forgot_browser": forgotten > 0,
                                                                         "by": _ctx(info)})

    async def revoke_others(self, principal: Principal, code: str, info: ClientInfo, *, everywhere: bool = False) -> int:
        """End every other session (or, with `everywhere`, every session including this one) and forget their browsers. Needs a fresh authenticator code."""
        await self._check_authenticator(principal.account_id, code, info)
        async with self.db.console_tx() as c:
            mine = await (await c.execute("SELECT device_id FROM admin_sessions WHERE id=%s", (principal.session_id,))).fetchone()
            where = "account_id=%s AND stage='session' AND revoked_at IS NULL AND expires_at > now()" + ("" if everywhere else " AND id <> %s")
            args: tuple[Any, ...] = (principal.account_id,) if everywhere else (principal.account_id, principal.session_id)
            cur = await c.execute(f"UPDATE admin_sessions SET revoked_at = now(), ended_reason = 'revoked_all' WHERE {where}", args)    # noqa: S608
            ended = cur.rowcount
            await self._forget(c, principal.account_id, keep=None if everywhere else (mine["device_id"] if mine else None))
        await self._audit("admin.sessions_revoked_all" if everywhere else "admin.sessions_revoked_others", principal.account_id, {"ended": ended, "by": _ctx(info)})
        return int(ended)

    async def _forget(self, c: Any, acct_id: uuid.UUID, *, keep: str | None, only: str | None = None) -> int:
        """Stop trusting browsers: one (`only`), or all but `keep`. Their cookies then need the WhatsApp code again."""
        k = await (await c.execute("SELECT trusted_devices FROM admin_credentials WHERE account_id=%s FOR UPDATE", (acct_id,))).fetchone()
        if k is None:
            return 0
        before = k["trusted_devices"]
        after = [d for d in before if (d["h"][:16] != only if only else d["h"][:16] == keep)]
        await c.execute("UPDATE admin_credentials SET trusted_devices=%s WHERE account_id=%s", (jsonb(after), acct_id))
        return len(before) - len(after)
