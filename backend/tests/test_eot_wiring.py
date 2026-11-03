"""What the turn worker tells turn detection: the customer's burst timing and whether the AI's last message was a question (ADR 0036)."""
from __future__ import annotations

from salesai.modules.conversations.eot import EotDecision, HeuristicEOT
from tests.test_conversation_flow import shop_with_catalog
from tests.world import settle


class Spy(HeuristicEOT):
    def __init__(self) -> None:
        self.seen = []

    async def decide(self, inp) -> EotDecision:
        self.seen.append(inp)
        return await super().decide(inp)


async def test_turn_detection_is_told_the_burst_timing_and_whether_the_ai_had_just_asked_a_question(world):
    shop, _ = await shop_with_catalog(world)
    spy = Spy()
    old = world.rt.turns.eot
    world.rt.turns.eot = spy
    try:
        phone = "+919800050001"
        await world.customer_says(shop, phone, "silk saree price?")
        await settle(world, shop, phone)
        mine = [i for i in spy.seen if i.texts and i.texts[0] == "silk saree price?"]       # the database is shared: other tests' chats may be decided meanwhile
        assert mine and mine[0].ai_asked_question is False and len(mine[0].message_times) == 1
        async with world.rt.db.tenant(shop.business_id) as c:                     # the AI's last message was a question
            await c.execute("""INSERT INTO messages (business_id, conversation_id, direction, sender, kind, body, status, created_at)
                               SELECT business_id, conversation_id, 'out', 'ai', 'text', 'Which colour would you like?', 'sent', now()
                               FROM messages WHERE direction='in' AND body='silk saree price?' ORDER BY created_at DESC LIMIT 1""")
        spy.seen.clear()
        await world.customer_says(shop, phone, "red")
        await settle(world, shop, phone, want=2)
        mine = [i for i in spy.seen if i.texts and i.texts[-1] == "red"]
        assert mine and all(i.ai_asked_question for i in mine)
    finally:
        world.rt.turns.eot = old
