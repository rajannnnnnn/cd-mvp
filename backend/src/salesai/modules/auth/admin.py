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

Authenticator codes work once (a used step is remembered), five wrong codes of any kind lock the account for fifteen minutes. The founder is three
deployment settings (phone, email, authenticator secret, ADR 0056); the database holds none of them, and a login must prove as much as its risk needs
(routine: the authenticator; elevated: plus one WhatsApp or email code; high: plus both). Tokens are signed with `ADMIN_JWT_SECRET`, which the public application does not hold, so it can
neither mint nor accept them. There is no refresh: a session ends at its fixed lifetime and is extended only by another fresh authenticator code.
Enrolment is a command run on the server (it prints a QR code), never a web page, so the first factor cannot be set up by whoever reaches the site."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import jwt

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
from salesai.modules.mail import render as render_mail
from salesai.modules.tenants import platform_audit
from salesai.phone import wa_id

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
CHANNELS = ("whatsapp", "email")           # the out-of-band proofs: a code to the founder's WhatsApp, a code to the founder's email
NEEDS = {"routine": 0, "elevated": 1, "high": 2}     # how many different channels each risk level must prove (ADR 0056)
HIGH_REASONS = {"first_sign_in", "wrong_codes"}      # any of these makes a login high-risk; so does a new browser together with a new network
RESEND_S = 30                                        # the wait before another code is sent on the same channel
EMAIL_CODE_MINUTES = 10
EMAIL_CODE_TRIES = 5


def _risk(reasons: list[str]) -> str:
    """How much proof this login must give (ADR 0056). Routine: the authenticator alone. Elevated: plus one code (WhatsApp or email). High: plus both."""
    if not reasons:
        return "routine"
    if HIGH_REASONS & set(reasons) or {"new_device", "new_network"} <= set(reasons):
        return "high"
    return "elevated"


def _secret(settings: Settings) -> str:
    """The founder's authenticator secret: a deployment setting, never stored (ADR 0056)."""
    if not settings.founder_totp_secret:
        raise ValueError("FOUNDER_TOTP_SECRET is not set")
    return settings.founder_totp_secret


def identity_fingerprint(settings: Settings) -> str:
    """A short hash of the three settings that are the founder. A session carries it, so it ends the moment any of them changes in the deployment."""
    raw = "|".join([settings.founder_phone, settings.founder_email.lower(), settings.founder_totp_secret])
    return hashlib.sha256(b"chatdeal-admin-identity|" + raw.encode()).hexdigest()[:24]


def _device_hash(token: str) -> str:
    return hashlib.sha256(b"chatdeal-admin-device|" + token.encode()).hexdigest()


def _ctx(info: ClientInfo) -> dict[str, Any]:
    """What goes on the audit trail about where a request came from."""
    return {"ip": info.ip, "device": describe_device(info.user_agent), "where": where_label(info.ip, info.geo), "user_agent": (info.user_agent or "")[:300]}


def _identity_set(settings: Settings) -> bool:
    return bool(settings.founder_phone and settings.founder_email and settings.founder_totp_secret)


async def ensure_founder(db: Database, settings: Settings) -> uuid.UUID:
    """Make sure the founder's account and state row exist, from the deployment's settings (idempotent). Any other operator is demoted: only the number in
    FOUNDER_PHONE can ever be one. Nothing secret is written. A new or restored database needs nothing else."""
    if not settings.founder_phone:
        raise ValueError("FOUNDER_PHONE is not set: the founder's number is a deployment setting (ADR 0047)")
    if not _identity_set(settings):
        raise ValueError("FOUNDER_EMAIL and FOUNDER_TOTP_SECRET are deployment settings too (ADR 0056)")
    async with db.console_tx() as c:
        acct = await (await c.execute(
            """INSERT INTO accounts (phone, name, platform_role) VALUES (%s,'Founder','operator')
               ON CONFLICT (phone) DO UPDATE SET platform_role = 'operator' RETURNING id, status""", (settings.founder_phone,))).fetchone()
        assert acct is not None
        if acct["status"] != "active":
            raise ValueError("the founder's account is blocked; unblock it in the database first")
        await c.execute("UPDATE accounts SET platform_role = NULL WHERE platform_role = 'operator' AND id <> %s", (acct["id"],))
        await c.execute("DELETE FROM admin_credentials WHERE account_id <> %s", (acct["id"],))
        await c.execute("INSERT INTO admin_credentials (account_id) VALUES (%s) ON CONFLICT (account_id) DO NOTHING", (acct["id"],))
    return uuid.UUID(str(acct["id"]))


async def enroll_admin(db: Database, settings: Settings) -> str:
    """Start the founder's sign-in state afresh: every console session ends, every trusted browser is forgotten, counters and the lock are cleared, and the
    next sign-in is a first one. Returns the authenticator secret from the deployment's settings (to put in a new phone); nothing is stored."""
    acct_id = await ensure_founder(db, settings)
    async with db.console_tx() as c:
        await c.execute(
            """UPDATE admin_credentials SET enrolled_at = now(), last_used_step = 0, failed_attempts = 0, locked_until = NULL, failed_since_login = 0,
                   last_failed_at = NULL, last_login_at = NULL, last_login_ip = NULL, was_locked = false, trusted_devices = '[]', totp_secret_enc = NULL
               WHERE account_id = %s""", (acct_id,))
        await c.execute("UPDATE admin_sessions SET revoked_at = now(), ended_reason = 'enrolment' WHERE revoked_at IS NULL")
        await platform_audit(c, actor_account_id=acct_id, actor_label="admin", action="admin.enrolled", business_id=None)
    return _secret(settings)


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
    reason: str | None = None          # why more proof is needed, in the founder's terms (shown to the founder after the authenticator passed)
    device_token: str | None = None    # the trusted-browser token to keep in a cookie after a full sign-in
    risk: str | None = None            # elevated | high
    needs: int = 0                     # how many more different codes (WhatsApp, email) the ticket still needs
    channels: list[str] = field(default_factory=list)   # the channels still usable
    done: list[str] = field(default_factory=list)       # the channels already proven


class AdminAuth:
    def __init__(self, db: Database, settings: Settings, auth: AuthService, *, clock: Callable[[], float] = time.time,
                 deliver: Callable[[EmailMessage], str | None] | None = None):
        self.db, self.s, self.auth, self.clock = db, settings, auth, clock
        self.deliver = deliver               # sends the emailed sign-in code (the console is the one part that sends mail on its own request path)

    # ------------------------------------------------------------------ small helpers
    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), UTC)

    def _token(self, acct_id: uuid.UUID, sid: uuid.UUID, role: str, ttl: int) -> str:
        now = int(self.clock())
        return jwt.encode({"sub": str(acct_id), "sid": str(sid), "role": role, "aud": AUDIENCE, "typ": "admin", "iat": now, "exp": now + ttl},
                          self.s.admin_jwt_secret, algorithm="HS256")

    async def _founder(self) -> dict[str, Any] | None:
        """The one founder: the number, email and authenticator secret in the deployment's settings. Their account and state row are created here if the
        database does not have them yet (a new or restored database). Nobody else exists."""
        s = self.s
        if not s.admin_jwt_secret or not _identity_set(s) or s.otp_accept_any:
            return None
        sql = """SELECT a.id, a.phone FROM accounts a JOIN admin_credentials k ON k.account_id = a.id
                 WHERE a.phone=%s AND a.platform_role='operator' AND a.status='active'"""
        async with self.db.console_tx() as c:
            row = await (await c.execute(sql, (s.founder_phone,))).fetchone()
        if row is None:
            await ensure_founder(self.db, s)
            async with self.db.console_tx() as c:
                row = await (await c.execute(sql, (s.founder_phone,))).fetchone()
        return {**dict(row), "email": s.founder_email} if row else None

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
            await self._audit("admin.sign_in_failed", None, {"step": "authenticator", "founder": "not set up", **_ctx(info)})
            raise AuthError("invalid_code", WRONG)
        await self._check_authenticator(founder["id"], code, info)
        reasons = await self._suspicion(founder["id"], info.ip, device)
        risk = _risk(reasons)
        if risk == "routine":
            return await self._complete(founder["id"], info, device, method="authenticator", reason=None, risk=risk)
        ticket, row = await self._ticket(founder["id"], info, risk, reasons)
        await self._audit("admin.step_up_required", founder["id"], {"reasons": reasons, "risk": risk, "needs": NEEDS[risk], **_ctx(info)})
        return self._progress(ticket, row)

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

    def _progress(self, ticket: AdminSession, row: dict[str, Any]) -> SignIn:
        """Where a ticket stands: how many more codes, on which channels, what is already proven."""
        done = [p for p in (row["proofs"] or "").split(",") if p in CHANNELS]
        left = max(int(row["needs"] or 0) - len(done), 0)
        secs = max(int((row["expires_at"] - self._now()).total_seconds()), 0)
        reason = (row["reasons"] or "").split(",")[0] or None
        return SignIn(ticket=AdminSession(ticket.access_token, secs, "admin_ticket"), reason=reason, risk=row["risk"], needs=left,
                      channels=[c for c in CHANNELS if c not in done] if left else [], done=done)

    def _trusted(self, devices: list[dict[str, str]], token: str | None) -> bool:
        if not token:
            return False
        h = _device_hash(token)
        oldest = self._now() - timedelta(days=self.s.admin_device_trust_days)
        return any(d["h"] == h and datetime.fromisoformat(d["at"]) > oldest for d in devices)

    async def _ticket(self, acct_id: uuid.UUID, info: ClientInfo, risk: str, reasons: list[str]) -> tuple[AdminSession, dict[str, Any]]:
        sid = uuid.uuid4()
        async with self.db.console_tx() as c:
            row = await (await c.execute(
                """INSERT INTO admin_sessions (id, account_id, stage, ip, expires_at, risk, reasons, needs, proofs, identity, geo, city, region, language, user_agent, device_label)
                   VALUES (%s,%s,'ticket',%s,%s,%s,%s,%s,'authenticator',%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (sid, acct_id, info.ip, self._now() + timedelta(seconds=TICKET_S), risk, ",".join(reasons), NEEDS[risk], identity_fingerprint(self.s), info.geo, info.city,
                 info.region, info.language, (info.user_agent or "")[:300], describe_device(info.user_agent)))).fetchone()
        assert row is not None
        return AdminSession(self._token(acct_id, sid, "admin_ticket", TICKET_S), TICKET_S, "admin_ticket"), dict(row)

    async def _complete(self, acct_id: uuid.UUID, info: ClientInfo, device: str | None, *, method: str, reason: str | None, risk: str = "routine") -> SignIn:
        """A fully proven sign-in: start the session (recording where it came from and every proof used), reset the suspicion counters and trust this
        browser (a new token, or the same one renewed). A sign-in that needed more than the authenticator tells the founder."""
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
        session, sid = await self._start_session(acct_id, info, _device_hash(token)[:16], method, reason, usual_network, risk)
        ctx = _ctx(info)
        await self._audit("admin.signed_in", acct_id, {"new_browser": known is None, "method": method, "risk": risk, "reason": reason, "session": str(sid), **ctx})
        if risk != "routine" and reason != "first_sign_in":
            await self._alert("high", "admin_new_sign_in", f"Console sign-in judged {risk} risk ({(reason or '').replace('_', ' ')}), proven by {method.replace('+', ' + ')}: {ctx['device']}, {ctx['where']}",
                              {"risk": risk, "reason": reason, "method": method, "session": str(sid), **ctx}, str(sid))
        return SignIn(session=session, device_token=token)

    # ------------------------------------------------------------------ the extra proof: codes to the founder's WhatsApp and email
    async def _ticket_for(self, ticket: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        claims, row = await self._load(ticket, "ticket")
        founder = await self._founder()
        if founder is None or str(founder["id"]) != claims["sub"] or row["identity"] != identity_fingerprint(self.s):
            raise AuthError("invalid_token", "Invalid credentials.")
        return claims, row, founder

    async def ticket_status(self, ticket: str) -> SignIn:
        """Where a sign-in stands, so a page refresh in the middle of it loses nothing. Sends nothing."""
        _, row, _ = await self._ticket_for(ticket)
        return self._progress(AdminSession(ticket, 0, "admin_ticket"), row)

    async def send_step_up_code(self, ticket: str, channel: str, info: ClientInfo) -> int:
        """Send one code, on one channel, to the founder. Needs a ticket (which only a correct authenticator code can obtain), so guessing can never make the
        platform message or email anyone. Returns the seconds to wait before asking again."""
        _, row, founder = await self._ticket_for(ticket)
        if channel not in CHANNELS:
            raise AuthError("invalid_channel", "Choose WhatsApp or email.", 422)
        done = [p for p in (row["proofs"] or "").split(",") if p in CHANNELS]
        if channel in done or len(done) >= int(row["needs"] or 0):
            raise AuthError("already_done", "That check is already done.", 409)
        last = (row["sent"] or {}).get(channel)
        if last and (self._now() - datetime.fromisoformat(last)).total_seconds() < RESEND_S:
            raise AuthError("slow_down", "Wait a moment before asking for another code.", 429)
        if channel == "whatsapp":
            await self.auth.request_otp(founder["phone"], info.ip)
        else:
            await self._send_email_code(founder, row["id"], info)
        async with self.db.console_tx() as c:
            await c.execute("UPDATE admin_sessions SET sent = sent || jsonb_build_object(%s::text, %s::text) WHERE id=%s", (channel, self._now().isoformat(), row["id"]))
        await self._audit("admin.step_up_code_sent", founder["id"], {"channel": channel, **_ctx(info)})
        return RESEND_S

    async def _send_email_code(self, founder: dict[str, Any], ticket_id: uuid.UUID, info: ClientInfo) -> None:
        code = f"{secrets.randbelow(10**8):08d}"
        async with self.db.console_tx() as c:
            await c.execute("DELETE FROM admin_email_codes WHERE ticket_id=%s AND used_at IS NULL", (ticket_id,))
            await c.execute("INSERT INTO admin_email_codes (account_id, ticket_id, code_hash, expires_at) VALUES (%s,%s,%s,%s)",
                            (founder["id"], ticket_id, self._email_hash(ticket_id, code), self._now() + timedelta(minutes=EMAIL_CODE_MINUTES)))
        where, device = where_label(info.ip, info.geo), describe_device(info.user_agent)
        if self.s.otp_channel == "simulator":          # development and tests: the code lands in the simulator's inbox like the WhatsApp one, nothing is sent
            async with self.db.console_tx() as c:
                await c.execute(
                    """INSERT INTO sim_messages (phone, business_phone, direction, kind, body, template_name) VALUES (%s,'platform','to_user','text',%s,'admin_login_code_email')""",
                    (wa_id(founder["phone"]), f"Your ChatDeal console sign-in code is {code} (by email to {founder['email']}). It works once and expires in {EMAIL_CODE_MINUTES} minutes."))
            return
        if self.deliver is None:
            raise AuthError("email_failed", "We could not send the code. Try WhatsApp instead.", 502)
        mail = render_mail("login_code", {"code": code, "minutes": EMAIL_CODE_MINUTES, "where": where, "device": device}, self.s.owner_site_url)
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = " ".join(mail.subject.split())[:200], " ".join(self.s.admin_email_from.split()), founder["email"]
        msg.set_content(mail.text)
        msg.add_alternative(mail.html, subtype="html")
        try:
            await asyncio.wait_for(asyncio.to_thread(self.deliver, msg), timeout=12)
        except Exception as e:  # noqa: BLE001  a failed send must never let the sign-in through, and must say so
            log.warning("console sign-in code email failed: %s", type(e).__name__)
            async with self.db.console_tx() as c:
                await c.execute("DELETE FROM admin_email_codes WHERE ticket_id=%s AND used_at IS NULL", (ticket_id,))
            raise AuthError("email_failed", "We could not send the code. Try WhatsApp instead.", 502) from None

    def _email_hash(self, ticket_id: uuid.UUID, code: str) -> str:
        return hashlib.sha256(f"chatdeal-admin-email|{self.s.admin_jwt_secret}|{ticket_id}|{code}".encode()).hexdigest()

    async def _consume_email_code(self, ticket_id: uuid.UUID, code: str) -> bool:
        """Accept the emailed code once. A wrong try is counted and committed; the sixth try is refused whatever the code."""
        async with self.db.console_tx() as c:
            row = await (await c.execute(
                """SELECT id, code_hash, attempts FROM admin_email_codes WHERE ticket_id=%s AND used_at IS NULL AND expires_at > now()
                   ORDER BY id DESC LIMIT 1 FOR UPDATE""", (ticket_id,))).fetchone()
            if row is None or row["attempts"] >= EMAIL_CODE_TRIES:
                return False
            await c.execute("UPDATE admin_email_codes SET attempts = attempts + 1 WHERE id=%s", (row["id"],))
            ok = bool(code.isascii() and code.isdigit()) and hmac.compare_digest(row["code_hash"], self._email_hash(ticket_id, code))
            if ok:
                await c.execute("UPDATE admin_email_codes SET used_at = now() WHERE id=%s", (row["id"],))
            return ok

    async def verify_step_up(self, ticket: str, code: str, channel: str, info: ClientInfo, device: str | None) -> SignIn:
        """One code on one channel. Either the sign-in is complete (a session), or the ticket needs more (the same ticket, with its progress)."""
        _, row, founder = await self._ticket_for(ticket)
        acct_id = uuid.UUID(str(founder["id"]))
        if channel not in CHANNELS:
            raise AuthError("invalid_channel", "Choose WhatsApp or email.", 422)
        await self._refuse_if_locked(acct_id)
        done = [p for p in (row["proofs"] or "").split(",") if p in CHANNELS]
        if channel in done or len(done) >= int(row["needs"] or 0):
            raise AuthError("already_done", "That check is already done.", 409)
        ok = await self.auth.consume_code(founder["phone"], code) if channel == "whatsapp" else await self._consume_email_code(row["id"], code)
        if not ok:
            await self._fail(acct_id, f"{channel}_code", info)
            raise AuthError("invalid_code", WRONG)
        done = [*done, channel]
        async with self.db.console_tx() as c:
            fresh = await (await c.execute("UPDATE admin_sessions SET proofs = proofs || %s WHERE id=%s RETURNING *", ("," + channel, row["id"]))).fetchone()
        assert fresh is not None
        if len(done) < int(row["needs"] or 0):
            return self._progress(AdminSession(ticket, 0, "admin_ticket"), dict(fresh))
        async with self.db.console_tx() as c:
            await c.execute("UPDATE admin_sessions SET revoked_at = now() WHERE id=%s", (row["id"],))        # a ticket is used once
        reason = (row["reasons"] or "").split(",")[0] or "new_device"
        return await self._complete(acct_id, info, device, method="authenticator+" + "+".join(done), reason=reason, risk=row["risk"])

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
                step = totp.verify(_secret(self.s), code.strip(), now=self.clock(), after_step=int(k["last_used_step"]))
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
                             usual_network: bool | None = None, risk: str = "routine") -> tuple[AdminSession, uuid.UUID]:
        ttl = self.s.admin_session_minutes * 60
        sid = uuid.uuid4()
        now = self._now()
        async with self.db.console_tx() as c:
            await c.execute(
                """INSERT INTO admin_sessions (id, account_id, stage, ip, expires_at, user_agent, device_label, device_id, geo, network, method, step_up_reason, last_seen_at,
                                               usual_network, identity, risk, proofs, reasons, city, region, language)
                   VALUES (%s,%s,'session',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (sid, acct_id, info.ip, now + timedelta(seconds=ttl), (info.user_agent or "")[:300], describe_device(info.user_agent), device_id, info.geo,
                 network_of(info.ip), method, reason, now, usual_network, identity_fingerprint(self.s), risk, method, reason, info.city, info.region, info.language))
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
        if acct is None or not self.s.founder_phone or acct["phone"] != self.s.founder_phone or row.get("identity") != identity_fingerprint(self.s):
            raise AuthError("invalid_token", "Invalid credentials.")
        return Principal(uuid.UUID(claims["sub"]), row["id"], None, "operator", acct["phone"])

    async def extend(self, principal: Principal, code: str, info: ClientInfo) -> AdminSession:
        await self._check_authenticator(principal.account_id, code, info)
        async with self.db.console_tx() as c:
            old = await (await c.execute("SELECT * FROM admin_sessions WHERE id=%s", (principal.session_id,))).fetchone()
            await c.execute("UPDATE admin_sessions SET revoked_at = now(), ended_reason = 'extended' WHERE id=%s", (principal.session_id,))
        assert old is not None
        session, _ = await self._start_session(principal.account_id, ClientInfo(old["ip"], old["user_agent"], old["geo"], old["city"], old["region"], old["language"]),
                                                old["device_id"], "extended", old["step_up_reason"], old["usual_network"], old["risk"] or "routine")
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
                "method": r["method"], "risk": r["risk"], "proofs": r["proofs"], "city": r["city"], "region": r["region"], "language": r["language"], "step_up_reason": r["step_up_reason"], "started_at": r["created_at"], "last_seen_at": r["last_seen_at"],
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
