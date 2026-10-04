"""Owner-facing message text (English / Hindi). Pure functions."""
from __future__ import annotations

from typing import Any

REASONS = {
    "unknown_answer": ("couldn't answer a question", "एक सवाल का जवाब नहीं दे सका"),
    "on_request_price": ("asked for a price that you quote personally", "ऐसी कीमत पूछी जो आप खुद बताते हैं"),
    "below_floor": ("is asking for a price lower than the AI may offer", "उस कीमत से कम माँग रहा है जो AI दे सकता है"),
    "customer_asked_human": ("asked to talk to you", "आपसे बात करना चाहता है"),
    "complaint": ("has a complaint", "ने शिकायत की है"),
    "high_value": ("is placing a high-value order", "बड़ा ऑर्डर दे रहा है"),
    "system_failure": ("could not be answered by the AI", "को AI जवाब नहीं दे सका"),
    "other": ("needs your attention", "को आपकी ज़रूरत है"),
}


def pick(lang: str, en: str, hi: str) -> str:
    return hi if lang == "hi" else en


def reason_text(reason: str, lang: str) -> str:
    en, hi = REASONS.get(reason, REASONS["other"])
    return pick(lang, en, hi)


def handoff_alert(lang: str, biz: str, customer: str, reason: str, note: str | None, code: str, last_message: str | None,
                  gap_code: str | None = None) -> tuple[str, list[str]]:
    """(free-form body, template params). Template: handoff_alert {{1}} biz {{2}} reason {{3}} customer."""
    why = reason_text(reason, lang)
    body = pick(lang, f"🔔 {biz}: {customer} {why}.", f"🔔 {biz}: {customer} {why}।")
    if note:
        body += f"\n{note[:200]}"
    if last_message:
        body += "\n" + pick(lang, f"Last message: “{last_message[:160]}”", f"आखिरी मैसेज: “{last_message[:160]}”")
    body += "\n" + pick(lang, f"Reply to them from your WhatsApp Business app. When done, send: done {code}",
                        f"अपने WhatsApp Business ऐप से उन्हें जवाब दें। पूरा होने पर भेजें: done {code}")
    if gap_code:
        body += "\n" + pick(lang, f"To teach me the answer for next time, send: answer {gap_code} <your answer>",
                            f"अगली बार के लिए जवाब सिखाने हेतु भेजें: answer {gap_code} <आपका जवाब>")
    return body, [biz, why, customer]


def deal_alert(lang: str, biz: str, kind: str, customer: str, details: str, code: str) -> tuple[str, list[str]]:
    label = pick(lang, "order" if kind == "order" else "store visit", "ऑर्डर" if kind == "order" else "दुकान विज़िट")
    body = pick(lang, f"🛍️ {biz}: new {label} from {customer}.\n{details}\nConfirm with: won {code}   or reject: lost {code}",
                f"🛍️ {biz}: {customer} से नया {label}।\n{details}\nपुष्टि करें: won {code}   या रद्द: lost {code}")
    return body, [biz, label, customer, details, code]


def help_text(lang: str) -> str:
    return pick(lang,
        "Commands:\n• stop — pause the AI for the whole business\n• start — resume the AI\n• status — today's numbers\n"
        "• won <code> / lost <code> — close a deal\n• done <code> — resolve a handoff\n• answer <code> <text> — answer a customer question\n"
        "• Or just tell me what to change: “add product Red saree 1500”, “hours: Mon-Sat 10-8”, “address: …”.",
        "कमांड:\n• stop — पूरे बिज़नेस के लिए AI रोकें\n• start — AI फिर चालू करें\n• status — आज के आँकड़े\n"
        "• won <code> / lost <code> — डील बंद करें\n• done <code> — हैंडऑफ़ पूरा करें\n• answer <code> <text> — ग्राहक के सवाल का जवाब दें\n"
        "• या सीधे बताइए क्या बदलना है: “add product Red saree 1500”, “hours: Mon-Sat 10-8”, “address: …”।")


def paused(lang: str) -> str:
    return pick(lang, "⏸️ The AI assistant is paused for all customers. Send *start* to resume.", "⏸️ AI असिस्टेंट सभी ग्राहकों के लिए रुका है। फिर चालू करने के लिए *start* भेजें।")


def resumed(lang: str) -> str:
    return pick(lang, "▶️ The AI assistant is back on and answering customers.", "▶️ AI असिस्टेंट फिर चालू है और ग्राहकों को जवाब दे रहा है।")


def summary_body(lang: str, m: dict[str, Any]) -> str:
    lines = [
        pick(lang, f"Conversations: {m['conversations']} ({m['new_customers']} new customers)", f"बातचीत: {m['conversations']} ({m['new_customers']} नए ग्राहक)"),
        pick(lang, f"Buying interest: {m['interested']}", f"खरीदने में रुचि: {m['interested']}"),
        pick(lang, f"Commitments captured: {m['commitments']} (pending your confirmation: {m['pending_deals']})", f"मिले कमिटमेंट: {m['commitments']} (आपकी पुष्टि बाकी: {m['pending_deals']})"),
        pick(lang, f"Complaints: {m['complaints']}", f"शिकायतें: {m['complaints']}"),
        pick(lang, f"Open handoffs: {m['open_handoffs']}", f"खुले हैंडऑफ़: {m['open_handoffs']}"),
        pick(lang, f"Questions I couldn't answer: {m['open_gaps']}", f"जिन सवालों का जवाब नहीं दे सका: {m['open_gaps']}"),
    ]
    return "\n".join(lines)


def disconnect_alert(lang: str, biz: str, number: str, reason: str) -> tuple[str, list[str]]:
    body = pick(lang, f"⚠️ {biz}: your WhatsApp number {number} is disconnected. The AI assistant has been paused. Reason: {reason}",
                f"⚠️ {biz}: आपका WhatsApp नंबर {number} डिस्कनेक्ट हो गया है। AI असिस्टेंट रोक दिया गया है। कारण: {reason}")
    return body, [biz, number, reason]
