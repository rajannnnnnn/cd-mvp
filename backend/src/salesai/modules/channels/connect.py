"""Gateway: connects a WhatsApp number the web component asked for (ADR 0025).

The web side validates the owner's request and writes `number_connect_requests`; this handler, on the gateway's send
queue, completes Embedded Signup with Meta and stores the token encrypted. It never raises for an ordinary failure: the
owner is waiting, so the outcome (including how the web side should answer) is written to the request and the web side
reads it. A request that is no longer pending, or has waited too long for Meta's one-time code to still be good, is
skipped. At-least-once delivery is therefore safe: running a request twice connects once."""
from __future__ import annotations

import logging
import uuid

import psycopg.errors as pgerr

from salesai.config import Settings
from salesai.db import Conn, Database
from salesai.modules.channels.embedded_signup import SignupError, complete_signup
from salesai.modules.tenants import TokenVault, add_cloud_number
from salesai.queue.base import Job

log = logging.getLogger("salesai.connect")
CODE_GOOD_FOR = "10 minutes"            # Meta's authorization code is short-lived; later than this the owner starts again


class NumberConnector:
    def __init__(self, db: Database, vault: TokenVault, settings: Settings):
        self.db, self.vault, self.s = db, vault, settings

    async def connect(self, job: Job) -> None:
        rid = uuid.UUID(str(job.spec.payload["request_id"]))
        async with self.db.system_tx() as c:
            req = await (await c.execute(
                f"SELECT *, (created_at < now() - interval '{CODE_GOOD_FOR}') AS stale FROM number_connect_requests WHERE id=%s FOR UPDATE", (rid,))).fetchone()  # noqa: S608
            if req is None or req["status"] != "pending":
                return                                                  # answered already, or the owner gave up
            if req["stale"] or not req["code"]:
                await self._fail(c, rid, 422, "code_exchange_failed", "Sign-up took too long. Please start again.")
                return
        try:
            res = await complete_signup(graph_base=self.s.meta_graph_base, version=self.s.meta_graph_version, app_id=self.s.meta_app_id,
                                        app_secret=self.s.meta_app_secret, code=req["code"], waba_id=req["waba_id"],
                                        phone_number_id=req["phone_number_id"], coexistence=req["coexistence"])
        except SignupError as e:
            async with self.db.system_tx() as c:
                await self._fail(c, rid, 502 if e.retryable else 422, e.code, e.message)
            return
        try:
            nid = await add_cloud_number(self.db, self.vault, req["business_id"], phone_number_id=res.phone_number_id, waba_id=res.waba_id,
                                         display_phone=res.display_phone, access_token=res.access_token,
                                         coexistence=res.coexistence, verified_name=res.verified_name)
        except pgerr.UniqueViolation:                                   # the number is already connected to a shop
            async with self.db.system_tx() as c:
                await self._fail(c, rid, 409, None, None)
            return
        async with self.db.tenant(req["business_id"]) as c:
            if res.quality_rating:
                await c.execute("UPDATE whatsapp_numbers SET quality_rating=%s WHERE id=%s", (res.quality_rating, nid))
        async with self.db.system_tx() as c:
            await c.execute("UPDATE number_connect_requests SET status='done', number_id=%s, code=NULL, finished_at=now() "
                            "WHERE id=%s AND status='pending'", (nid, rid))

    @staticmethod
    async def _fail(c: Conn, rid: uuid.UUID, status: int, code: str | None, message: str | None) -> None:
        await c.execute("UPDATE number_connect_requests SET status='failed', code=NULL, error_status=%s, error_code=%s, error_message=%s, "
                        "finished_at=now() WHERE id=%s AND status='pending'", (status, code, message, rid))
