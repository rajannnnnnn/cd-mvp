"""Alert routing: pushes operator alerts (disconnected numbers, dead letters, failed sends, quality drops) to the platform
team through configured sinks, so nobody has to watch the console. Public interface of the module."""
from salesai.modules.alerting.router import AlertRouter, Sink  # noqa: F401
from salesai.modules.alerting.webhook_sink import ping_outside  # noqa: F401
