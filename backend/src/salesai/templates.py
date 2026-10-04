"""Approved WhatsApp message templates (used when the 24-hour window is closed). One registry shared by
the real channel (template name + params) and the simulator (renders the same text)."""
from __future__ import annotations

import re

TEMPLATES: dict[str, dict[str, str]] = {
    "otp_login": {
        "en": "{{1}} is your Sales AI login code. It is valid for 5 minutes. Do not share it with anyone.",
        "hi": "{{1}} आपका Sales AI लॉगिन कोड है। यह 5 मिनट तक मान्य है। इसे किसी से साझा न करें।",
    },
    "handoff_alert": {
        "en": "🔔 {{1}}: a customer needs you.\nReason: {{2}}\nCustomer: {{3}}\nReply to them from your WhatsApp Business app, or open the dashboard.",
        "hi": "🔔 {{1}}: एक ग्राहक को आपकी ज़रूरत है।\nकारण: {{2}}\nग्राहक: {{3}}\nअपने WhatsApp Business ऐप से जवाब दें या डैशबोर्ड खोलें।",
    },
    "deal_alert": {
        "en": "🛍️ {{1}}: new {{2}} captured.\nCustomer: {{3}}\nDetails: {{4}}\nPlease confirm it in the dashboard or reply 'won {{5}}'.",
        "hi": "🛍️ {{1}}: नया {{2}} मिला।\nग्राहक: {{3}}\nविवरण: {{4}}\nकृपया डैशबोर्ड में पुष्टि करें या 'won {{5}}' भेजें।",
    },
    "daily_summary": {
        "en": "📊 {{1}} — daily summary\n{{2}}",
        "hi": "📊 {{1}} — दैनिक सारांश\n{{2}}",
    },
    "disconnect_alert": {
        "en": "⚠️ {{1}}: your WhatsApp number {{2}} is disconnected. The AI assistant has been paused. Reason: {{3}}",
        "hi": "⚠️ {{1}}: आपका WhatsApp नंबर {{2}} डिस्कनेक्ट हो गया है। AI असिस्टेंट रोक दिया गया है। कारण: {{3}}",
    },
}

_PH = re.compile(r"\{\{(\d+)\}\}")


def render(name: str, language: str, params: list[str]) -> str:
    body = TEMPLATES.get(name, {}).get(language) or TEMPLATES.get(name, {}).get("en") or name
    return _PH.sub(lambda m: params[int(m.group(1)) - 1] if int(m.group(1)) <= len(params) else "", body)
