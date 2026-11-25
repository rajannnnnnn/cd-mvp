"""Email the platform sends: every template in one file (templates.py), the shared look (layout.py), and the outbox that sends mail to shop
owners without ever holding anything up (outbox.py). Public interface of the module."""
from salesai.modules.mail.outbox import MailDispatcher, enqueue  # noqa: F401
from salesai.modules.mail.templates import (  # noqa: F401
    CATALOG,
    Rendered,
    founder_alert_html,
    founder_digest_html,
    render,
)
