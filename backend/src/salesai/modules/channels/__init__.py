"""Channels module public interface."""
from typing import Any

from salesai.modules.channels.base import (  # noqa: F401
    AccountEvent,
    ChannelEvent,
    EchoMessage,
    InboundMessage,
    MessagingChannel,
    NumberRef,
    SendResult,
    StatusUpdate,
)
from salesai.modules.channels.embedded_signup import (  # noqa: F401
    SignupError,
    SignupResult,
    complete_signup,
)
from salesai.modules.channels.registry import ChannelRegistry  # noqa: F401


def parse_webhook(payload: dict[str, Any]) -> list[ChannelEvent]:
    """Normalize a provider webhook payload into channel-neutral events. Today the only provider that posts
    webhooks is the WhatsApp Cloud API (the simulator posts the same signed format); a new channel adds its
    own parser here, and conversation code never learns the provider's wire format."""
    from salesai.modules.channels.whatsapp import parse_webhook as _whatsapp  # noqa: PLC0415
    return _whatsapp(payload)
