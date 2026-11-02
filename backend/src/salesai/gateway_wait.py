"""How the web component waits for the gateway (ADR 0025).

The web side never sends to Meta. It writes a request and then looks at the request row until the gateway has answered.
Written once, because every such request has the same shape: look, and if still pending pump, until a deadline; at the
deadline give up in a way that cannot race the gateway (the give-up only succeeds if the request is still pending), so a
gateway that answers late finds a request nobody is waiting for and does nothing."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

T = TypeVar("T")


async def wait_for_gateway(look: Callable[[], Awaitable[T | None]], give_up: Callable[[], Awaitable[T | None]],
                           pump: Callable[[], Awaitable[None]], timeout_s: float) -> T:
    """`look` returns the gateway's answer, or None while the request is pending. At the deadline `give_up` marks the
    request failed and returns that outcome, or returns None if the gateway answered in the meantime (then we read it)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while True:
        answer = await look()
        if answer is not None:
            return answer
        if loop.time() >= deadline:
            gave_up = await give_up()
            if gave_up is not None:
                return gave_up
            continue                                            # it answered between our last look and now
        await pump()
