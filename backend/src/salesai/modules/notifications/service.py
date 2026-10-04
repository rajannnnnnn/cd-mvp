"""Owner loop (M9): alerts to the owner's WhatsApp, owner commands, configuration by chat with read-back
confirmation, daily summary, number disconnection. The owner talks to the platform from their own number; the
24-hour window applies to them too: inside it we send free-form text, outside it an approved template."""
from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from salesai.db import Conn, Database, jsonb, required
from salesai.events.outbox import emit
from salesai.modules.agent import LLMProvider, LLMRequest, LLMUnavailable, prompts
from salesai.modules.catalog import PolicyIn, ProductIn, VariantIn, repo
from salesai.modules.channels import ChannelRegistry
from salesai.modules.handoffs import answer_gap, resolve_handoff
from salesai.modules.notifications import messages as M
from salesai.modules.notifications.models import ConfigChanges
from salesai.modules.sales import close_deal
from salesai.queue.base import Job
from salesai.rules import norm_keyword, window_open

log = logging.getLogger("salesai.owner")
PROMPT_DIR = __import__("pathlib").Path(__file__).parent / "prompts"


def short(u: uuid.UUID | str) -> str:
    return str(u).replace("-", "")[:6]


class OwnerLoop:
    def __init__(self, db: Database, channels: ChannelRegistry, llm: LLMProvider, config_model: str):
        self.db, self.channels, self.llm, self.config_model = db, channels, llm, config_model

    # ------------------------------------------------------------------ sending to owners
    async def _targets(self, c: Conn, bid: uuid.UUID, business_user_id: uuid.UUID | None) -> list[dict[str, Any]]:
        q = """SELECT bu.id, bu.name, bu.last_inbound_at, a.phone, a.language FROM business_users bu JOIN accounts a ON a.id=bu.account_id
               WHERE bu.notify AND (%s::uuid IS NULL OR bu.id=%s)"""
        return await (await c.execute(q, (business_user_id, business_user_id))).fetchall()

    async def send_to_owner(self, bid: uuid.UUID, user: dict[str, Any], purpose: str, body: str, template: str | None = None,
                            params: list[str] | None = None) -> None:
        """Free-form inside the owner's 24h window, an approved template outside it (INV-8 applies to every recipient)."""
        async with self.db.tenant(bid) as c:
            num = await (await c.execute(
                "SELECT id FROM whatsapp_numbers WHERE status='connected' ORDER BY created_at LIMIT 1")).fetchone()
        if num is None:        # e.g. the business number is the thing that disconnected: use the platform sender
            ref, channel = self.channels.platform_sender()
        else:
            ref, channel = await self.channels.number(bid, num["id"])
        to = user["phone"].lstrip("+")
        in_window = window_open(user["last_inbound_at"])
        use_template = (not in_window) and template is not None
        if not in_window and template is None:
            log.info("owner outside window and no template for %s; message not sent", purpose)
            return
        res = await (channel.send_template(ref, to, template or "", user["language"], params or []) if use_template else channel.send_text(ref, to, body))
        async with self.db.tenant(bid) as c:
            await c.execute(
                """INSERT INTO owner_messages (business_id, business_user_id, direction, kind, purpose, template_name, body, wa_message_id, status, error, sent_at)
                   VALUES (%s,%s,'out',%s,%s,%s,%s,%s,%s,%s, now())""",
                (bid, user["id"], "template" if use_template else "text", purpose, template if use_template else None, body, res.message_id,
                 "sent" if res.ok else "failed", None if res.ok else res.error))
        if not res.ok:
            await self._operator_alert(bid, "owner_alert_failed", f"Could not alert the owner: {res.error}", {"purpose": purpose})
            if res.retryable:
                raise RuntimeError(res.error or "owner alert failed")

    async def _operator_alert(self, bid: uuid.UUID | None, kind: str, message: str, detail: dict[str, Any], severity: str = "warning") -> None:
        async with self.db.system_tx() as s:
            await s.execute("INSERT INTO operator_alerts (business_id, severity, kind, message, detail) VALUES (%s,%s,%s,%s,%s)",
                            (bid, severity, kind, message, jsonb(detail)))

    # ------------------------------------------------------------------ notify (handoff / deal / generic) — owner.notifications:notify
    async def notify(self, job: Job) -> None:
        p, bid = job.spec.payload, job.spec.business_id
        assert bid is not None
        etype = p["_event"]["type"]
        async with self.db.tenant(bid) as c:
            biz = await (await c.execute("SELECT name FROM businesses WHERE id=%s", (bid,))).fetchone()
            users = await self._targets(c, bid, p.get("business_user_id"))
            built: dict[str, Any] = {}
            if etype == "handoff.created":
                h = await (await c.execute(
                    """SELECT h.id, h.reason, h.note, cu.name AS customer, cu.wa_id,
                              (SELECT body FROM messages m WHERE m.conversation_id = h.conversation_id AND m.direction='in' ORDER BY m.created_at DESC LIMIT 1) AS last_msg
                       FROM handoffs h JOIN conversations cv ON cv.id=h.conversation_id JOIN customers cu ON cu.id=cv.customer_id WHERE h.id=%s""",
                    (p["handoff_id"],))).fetchone()
                if h is None:
                    return
                gap = await (await c.execute(
                    "SELECT id FROM knowledge_gaps WHERE status='open' AND conversation_id=(SELECT conversation_id FROM handoffs WHERE id=%s) ORDER BY created_at DESC LIMIT 1",
                    (p["handoff_id"],))).fetchone() if h["reason"] == "unknown_answer" else None
                built = {"purpose": "handoff_alert", "template": "handoff_alert", "h": h, "gap": short(gap["id"]) if gap else None}
            elif etype == "deal.captured":
                d = await (await c.execute(
                    """SELECT d.id, d.kind, d.items, d.details, d.value, cu.name AS customer, cu.wa_id FROM deals d
                       JOIN conversations cv ON cv.id=d.conversation_id JOIN customers cu ON cu.id=cv.customer_id WHERE d.id=%s""", (p["deal_id"],))).fetchone()
                if d is None:
                    return
                built = {"purpose": "deal_alert", "template": "deal_alert", "d": d}
            elif etype == "owner.notification_requested":
                built = {"purpose": p["purpose"], "template": p["purpose"], "data": p.get("data", {})}
            else:
                return
        for u in users:
            lang = u["language"]
            if built["purpose"] == "handoff_alert":
                h = built["h"]
                body, params = M.handoff_alert(lang, biz["name"], h["customer"] or f"+{h['wa_id']}", h["reason"], h["note"], short(h["id"]), h["last_msg"], built["gap"])
            elif built["purpose"] == "deal_alert":
                d = built["d"]
                det = d["details"].get("address") or d["details"].get("time") or ""
                items = ", ".join(f"{i['qty']}× {i['name']}" for i in d["items"]) if d["items"] else ""
                body, params = M.deal_alert(lang, biz["name"], d["kind"], d["customer"] or f"+{d['wa_id']}", " — ".join(x for x in (items, det) if x), short(d["id"]))
            elif built["purpose"] == "disconnect_alert":
                data = built["data"]
                body, params = M.disconnect_alert(lang, biz["name"], data.get("number", ""), data.get("reason", ""))
            elif built["purpose"] == "daily_summary":
                body = f"📊 {biz['name']}\n" + built["data"]["text"]
                params = [biz["name"], built["data"]["text"]]
            else:
                body, params = built["data"].get("text", ""), []
            await self.send_to_owner(bid, u, built["purpose"], body, built["template"], params)

    # ------------------------------------------------------------------ owner chat — owner.notifications:owner_chat
    async def owner_chat(self, job: Job) -> None:
        p, bid = job.spec.payload, job.spec.business_id
        assert bid is not None
        async with self.db.tenant(bid) as c:
            m = required(await (await c.execute("SELECT * FROM owner_messages WHERE id=%s", (p["owner_message_id"],))).fetchone(), "m")
            u = required(await (await c.execute("""SELECT bu.id, bu.name, bu.last_inbound_at, bu.role, a.phone, a.language, a.id AS account_id
                                          FROM business_users bu JOIN accounts a ON a.id=bu.account_id WHERE bu.id=%s""", (m["business_user_id"],))).fetchone(), "owner")
        reply = await self.handle_owner_text(bid, u, m["body"] or "", m["kind"])
        if reply:
            await self.send_to_owner(bid, u, "chat", reply)

    async def handle_owner_text(self, bid: uuid.UUID, u: dict[str, Any], text: str, kind: str = "text") -> str | None:
        lang = u["language"]
        t = norm_keyword(text)
        words = t.split()
        if kind != "text" or not t:
            return M.pick(lang, "Please send your instruction as text for now.", "कृपया अभी अपना निर्देश टेक्स्ट में भेजें।")
        first = words[0]
        if t in ("stop", "pause", "rukjao", "ruko", "band", "band karo", "रुको", "रोको", "बंद"):
            async with self.db.tenant(bid) as c:
                await c.execute("UPDATE businesses SET ai_enabled=false WHERE id=%s", (bid,))
                await c.execute("INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity) VALUES (%s,%s,'owner','ai.paused_by_owner_chat','business')", (bid, u["account_id"]))
            return M.paused(lang)
        if t in ("start", "resume", "chalu", "chalu karo", "shuru", "चालू", "शुरू"):
            async with self.db.tenant(bid) as c:
                await c.execute("UPDATE businesses SET ai_enabled=true WHERE id=%s", (bid,))
                await c.execute("INSERT INTO audit_log (business_id, actor_account_id, actor_label, action, entity) VALUES (%s,%s,'owner','ai.resumed_by_owner_chat','business')", (bid, u["account_id"]))
            return M.resumed(lang)
        if t in ("help", "?", "menu", "madad", "मदद"):
            return M.help_text(lang)
        if t in ("status", "report", "summary", "sthiti"):
            return M.summary_body(lang, await self.metrics(bid))
        if first in ("won", "lost") and len(words) >= 2:
            return await self._close_deal_cmd(bid, u, first, words[1])
        if first in ("done", "resolve", "resolved") and len(words) >= 2:
            return await self._resolve_cmd(bid, u, words[1])
        if first == "answer" and len(words) >= 3:
            return await self._answer_cmd(bid, u, words[1], text.split(None, 2)[2])
        if t in ("yes", "confirm", "haan", "ha", "ok", "okay", "हाँ", "हां", "theek hai", "kar do"):
            return await self._decide_proposal(bid, u, True)
        if t in ("no", "cancel", "nahi", "nahin", "nahi karo", "नहीं", "रद्द"):
            return await self._decide_proposal(bid, u, False)
        return await self._propose_config(bid, u, text)

    # ---- commands
    async def metrics(self, bid: uuid.UUID) -> dict[str, int]:
        async with self.db.tenant(bid) as c:
            r = await (await c.execute(
                """SELECT
                     (SELECT count(*) FROM conversations WHERE last_inbound_at >= date_trunc('day', now())) AS conversations,
                     (SELECT count(*) FROM customers WHERE first_seen_at >= date_trunc('day', now())) AS new_customers,
                     (SELECT count(*) FROM conversations WHERE lead_stage IN ('interested','negotiating','ready_to_buy') AND updated_at >= date_trunc('day', now())) AS interested,
                     (SELECT count(*) FROM deals WHERE created_at >= date_trunc('day', now())) AS commitments,
                     (SELECT count(*) FROM deals WHERE status='pending') AS pending_deals,
                     (SELECT count(*) FROM handoffs WHERE reason='complaint' AND created_at >= date_trunc('day', now())) AS complaints,
                     (SELECT count(*) FROM handoffs WHERE status='open') AS open_handoffs,
                     (SELECT count(*) FROM knowledge_gaps WHERE status='open') AS open_gaps""")).fetchone()
        return {k: int(v) for k, v in r.items()}

    async def _close_deal_cmd(self, bid: uuid.UUID, u: dict[str, Any], status: str, code: str) -> str:
        lang = u["language"]
        async with self.db.tenant(bid) as c:
            row = await (await c.execute("SELECT id FROM deals WHERE replace(id::text,'-','') LIKE %s AND status='pending'", (code + "%",))).fetchone()
            if row is None:
                return M.pick(lang, f"No pending deal with code {code}.", f"कोड {code} वाली कोई लंबित डील नहीं मिली।")
            await close_deal(c, bid, row["id"], status, u["account_id"])
        return M.pick(lang, f"Deal {code} marked {status}. ✅", f"डील {code} {status} कर दी गई। ✅")

    async def _resolve_cmd(self, bid: uuid.UUID, u: dict[str, Any], code: str) -> str:
        lang = u["language"]
        async with self.db.tenant(bid) as c:
            row = await (await c.execute("SELECT id FROM handoffs WHERE replace(id::text,'-','') LIKE %s AND status='open'", (code + "%",))).fetchone()
            if row is None or not await resolve_handoff(c, bid, row["id"], u["account_id"]):
                return M.pick(lang, f"No open handoff with code {code}.", f"कोड {code} वाला कोई खुला हैंडऑफ़ नहीं मिला।")
        return M.pick(lang, "Handoff resolved. The AI can answer this customer again. ✅", "हैंडऑफ़ पूरा हुआ। अब AI इस ग्राहक को फिर जवाब दे सकता है। ✅")

    async def _answer_cmd(self, bid: uuid.UUID, u: dict[str, Any], code: str, answer: str) -> str:
        """Owner answers a knowledge gap. It becomes a business fact only after read-back confirmation."""
        lang = u["language"]
        async with self.db.tenant(bid) as c:
            gap = await (await c.execute("SELECT id, question FROM knowledge_gaps WHERE replace(id::text,'-','') LIKE %s AND status='open'", (code + "%",))).fetchone()
            if gap is None:
                return M.pick(lang, f"No open question with code {code}.", f"कोड {code} वाला कोई खुला सवाल नहीं मिला।")
            await self._store_proposal(c, bid, u["id"], f"Save as a fact: “{gap['question'][:120]}” → “{answer[:200]}”",
                                       {"ops": [{"op": "answer_gap", "gap_id": str(gap["id"]), "answer": answer}]})
        return M.pick(lang, f"Save this answer so I can use it with customers?\n“{answer[:200]}”\nReply *yes* to confirm or *no* to cancel.",
                      f"क्या यह जवाब सेव करूँ ताकि मैं ग्राहकों को बता सकूँ?\n“{answer[:200]}”\nपुष्टि के लिए *yes*, रद्द के लिए *no* भेजें।")

    # ---- configuration by chat (FR-CF-8)
    async def _store_proposal(self, c: Conn, bid: uuid.UUID, user_id: uuid.UUID, summary: str, changes: dict[str, Any]) -> None:
        await c.execute("UPDATE config_proposals SET status='cancelled', decided_at=now() WHERE business_user_id=%s AND status='pending'", (user_id,))
        await c.execute("INSERT INTO config_proposals (business_id, business_user_id, summary, changes) VALUES (%s,%s,%s,%s)", (bid, user_id, summary, jsonb(changes)))

    async def _propose_config(self, bid: uuid.UUID, u: dict[str, Any], text: str) -> str:
        lang = u["language"]
        async with self.db.tenant(bid) as c:
            prods = [r["name"] for r in await (await c.execute("SELECT name FROM products WHERE active")).fetchall()]
        pr = prompts.load("config", directory=PROMPT_DIR)
        try:
            res = await self.llm.generate(LLMRequest("config", pr.text, {"text": text, "products": prods}, self.config_model, pr.ref, max_tokens=700), ConfigChanges)
        except LLMUnavailable:
            return M.pick(lang, "I couldn't process that right now. Please try again in a minute.", "मैं अभी इसे प्रोसेस नहीं कर सका। कृपया एक मिनट बाद फिर कोशिश करें।")
        ch: ConfigChanges = res.output
        if not ch.ops:
            return ch.clarification or M.help_text(lang)
        lines = []
        for o in ch.ops:
            if o.op == "set_profile":
                lines.append(f"• {o.field}: {o.value}")
            elif o.op == "add_fact":
                lines.append(f"• fact: {o.value}")
            elif o.op == "add_product":
                lines.append(f"• new product “{o.name}” at ₹{o.price}")
            elif o.op == "set_price":
                lines.append(f"• price of “{o.product}” → ₹{o.price}")
        summary = "\n".join(lines)
        async with self.db.tenant(bid) as c:
            await self._store_proposal(c, bid, u["id"], summary, {"ops": [o.model_dump(mode="json", exclude_none=True) for o in ch.ops]})
        return M.pick(lang, f"I understood:\n{summary}\n\nReply *yes* to apply or *no* to cancel.", f"मैंने यह समझा:\n{summary}\n\nलागू करने के लिए *yes*, रद्द करने के लिए *no* भेजें।")

    async def _decide_proposal(self, bid: uuid.UUID, u: dict[str, Any], accept: bool) -> str:
        lang = u["language"]
        async with self.db.tenant(bid) as c:
            prop = await (await c.execute("SELECT * FROM config_proposals WHERE business_user_id=%s AND status='pending'", (u["id"],))).fetchone()
            if prop is None:
                return M.pick(lang, "There's nothing waiting for confirmation.", "पुष्टि के लिए कुछ भी बाकी नहीं है।")
            age = (datetime.now(UTC) - prop["created_at"]).total_seconds()
            if age > 3600:
                await c.execute("UPDATE config_proposals SET status='expired', decided_at=now() WHERE id=%s", (prop["id"],))
                return M.pick(lang, "That request expired. Please send it again.", "वह रिक्वेस्ट समाप्त हो गई। कृपया फिर से भेजें।")
            if not accept:
                await c.execute("UPDATE config_proposals SET status='cancelled', decided_at=now() WHERE id=%s", (prop["id"],))
                return M.pick(lang, "Cancelled. Nothing was changed.", "रद्द किया। कुछ भी नहीं बदला।")
            applied = await self.apply_ops(c, bid, u["account_id"], prop["changes"]["ops"])
            await c.execute("UPDATE config_proposals SET status='applied', decided_at=now() WHERE id=%s", (prop["id"],))
        return M.pick(lang, f"Done ✅ {applied}", f"हो गया ✅ {applied}")

    async def apply_ops(self, c: Conn, bid: uuid.UUID, actor: uuid.UUID, ops: list[dict[str, Any]]) -> str:
        done: list[str] = []
        for o in ops:
            kind = o["op"]
            if kind == "set_profile":
                val: Any = o["value"]
                if o["field"] == "payment_modes":
                    val = [x.strip() for x in re.split(r"[,/&]| and ", val) if x.strip()]
                await c.execute("UPDATE businesses SET profile = jsonb_set(profile, %s, %s::jsonb) WHERE id=%s", ([o["field"]], jsonb(val), bid))
                await repo.audit(c, bid, actor, "owner", "profile.updated", "business", str(bid), {"field": o["field"]})
                done.append(o["field"])
            elif kind == "add_fact":
                await c.execute("UPDATE businesses SET profile = jsonb_set(profile, '{facts}', COALESCE(profile->'facts','[]'::jsonb) || %s::jsonb) WHERE id=%s", (jsonb([o["value"]]), bid))
                await repo.audit(c, bid, actor, "owner", "profile.fact_added", "business", str(bid))
                done.append("fact")
            elif kind == "add_product":
                await repo.create_product(c, bid, ProductIn(name=o["name"], variants=[VariantIn(policy=PolicyIn(disclosure="fixed", list_price=Decimal(o["price"])))]), actor=actor)
                done.append(o["name"])
            elif kind == "set_price":
                row = await (await c.execute(
                    """SELECT v.id, pp.* FROM products p JOIN product_variants v ON v.product_id=p.id JOIN pricing_policies pp ON pp.variant_id=v.id
                       WHERE lower(p.name)=lower(%s) ORDER BY v.is_default DESC LIMIT 1""", (o["product"],))).fetchone()
                if row is None:
                    continue
                new_price = Decimal(o["price"])
                if row["disclosure"] == "on_request":
                    continue          # chat never turns an on-request item into a priced one
                pol = PolicyIn(disclosure=row["disclosure"], currency=row["currency"], list_price=new_price, range_min=row["range_min"], range_max=row["range_max"],
                               negotiable=row["negotiable"], ai_may_negotiate=False, concession_steps=0, round_to=row["round_to"])
                await repo.set_policy(c, bid, row["id"], pol, actor=actor)
                # price changed by chat: AI negotiation is switched off until the owner re-checks the floor in the dashboard
                done.append(f"{o['product']} ₹{new_price}")
            elif kind == "answer_gap":
                await answer_gap(c, bid, uuid.UUID(o["gap_id"]), o["answer"], actor, confirmed=True)
                done.append("answer saved")
        return ", ".join(done) if done else "no changes"

    # ------------------------------------------------------------------ daily summary
    async def daily_summary(self, job: Job) -> None:
        bid = job.spec.business_id
        assert bid is not None
        m = await self.metrics(bid)
        async with self.db.tenant(bid) as c:
            users = await self._targets(c, bid, None)
            biz = await (await c.execute("SELECT name FROM businesses WHERE id=%s", (bid,))).fetchone()
        for u in users:
            text = M.summary_body(u["language"], m)
            await self.send_to_owner(bid, u, "daily_summary", f"📊 {biz['name']} — {M.pick(u['language'], 'daily summary', 'दैनिक सारांश')}\n{text}", "daily_summary", [biz["name"], text])

    # ------------------------------------------------------------------ platform events: account / number status
    async def account_update(self, job: Job) -> None:
        p, bid = job.spec.payload, job.spec.business_id
        assert bid is not None
        kind, detail, pn = p["kind"], p.get("detail", {}), p.get("phone_number_id")
        async with self.db.tenant(bid) as c:
            num = await (await c.execute("SELECT * FROM whatsapp_numbers WHERE phone_number_id=%s", (pn,))).fetchone()
        if num is None:
            return
        if kind == "quality":
            rating = detail.get("quality_rating")
            if rating in ("green", "yellow", "red"):
                async with self.db.tenant(bid) as c:
                    await c.execute("UPDATE whatsapp_numbers SET quality_rating=%s WHERE id=%s", (rating, num["id"]))
                if rating != "green":
                    await self._operator_alert(bid, "quality_drop", f"WhatsApp quality rating is now {rating}", {"number": num["display_phone"]}, "critical" if rating == "red" else "warning")
        elif kind in ("disconnected", "restricted"):
            await self._set_status(bid, num, kind, detail.get("event", kind))
        elif kind == "reconnected":
            async with self.db.tenant(bid) as c:
                await c.execute("UPDATE whatsapp_numbers SET status='connected', status_reason=NULL WHERE id=%s", (num["id"],))

    async def number_status(self, job: Job) -> None:
        p, bid = job.spec.payload, job.spec.business_id
        assert bid is not None
        async with self.db.tenant(bid) as c:
            num = await (await c.execute("SELECT * FROM whatsapp_numbers WHERE id=%s", (p["whatsapp_number_id"],))).fetchone()
        if num and p["status"] in ("disconnected", "restricted"):
            await self._alert_disconnected(bid, num, num.get("status_reason") or p["status"])

    async def _set_status(self, bid: uuid.UUID, num: dict[str, Any], status: str, reason: str) -> None:
        async with self.db.tenant(bid) as c:
            await c.execute("UPDATE whatsapp_numbers SET status=%s, status_reason=%s WHERE id=%s", (status, reason, num["id"]))
        await self._alert_disconnected(bid, num, reason)

    async def _alert_disconnected(self, bid: uuid.UUID, num: dict[str, Any], reason: str) -> None:
        """FR-ON-4: stop the AI (ai_block_reason sees the disconnected number) and alert owner + operator."""
        await self._operator_alert(bid, "number_disconnected", f"WhatsApp number {num['display_phone']} disconnected: {reason}", {"number": num["display_phone"], "reason": reason}, "critical")
        async with self.db.tenant(bid) as c:
            await emit(c, "owner.notification_requested", {"purpose": "disconnect_alert", "data": {"number": num["display_phone"], "reason": reason}},
                       business_id=bid, ordering_key=f"biz:{bid}")
