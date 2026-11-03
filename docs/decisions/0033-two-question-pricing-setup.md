# 0033 Pricing setup becomes two questions

**Status:** accepted · 2026-11-02 · founder: "pricing things are saturated, and configuring from business user pov is too confusing"

## Context

The pricing model is correct and the engine is well tested. The **setup** is not usable by the people it
is for. A `pricing_policies` row exposes `disclosure` (five modes), `list_price`, `range_min`,
`range_max`, `negotiable`, `ai_may_negotiate`, `concession_steps`, `concession_requires` and `round_to`,
plus a private floor in its own table. That is up to **ten decisions per product**, before the owner has
sold anything. A shop with fifty products cannot do this, and a shop owner should not have to learn what
a concession step is.

The founder's UX direction is that the tenant is a busy business owner, not a technical user, and
anything needing a manual is a defect. This is the clearest instance of that defect in the product.

Nothing here changes the data model, the API or the pricing engine. Only what the owner is **asked**
changes. INV-2, INV-3 and INV-4 are untouched, which is what makes this safe to do now.

## Options

1. **Leave it; write better help text.** Help text does not fix ten decisions per product.
2. **Remove modes.** Would break real selling patterns — "from ₹X" and "ask me" are how these shops
   actually quote, and they are PRD requirements.
3. **Ask two questions and derive the rest**, keeping every field available behind an advanced section.

## Decision

Option 3.

**Question 1 — "What is the price?"** The owner is **not asked to choose a mode first.** They type what
they would say to a customer, and the mode is inferred:

| What they enter | Mode stored |
|---|---|
| `450` | `fixed` |
| `from 450` or the From field | `starts_from` |
| `450-600` | `range` |
| "I'll tell them myself" | `on_request` |

Choosing a taxonomy before entering a number is the step that makes this feel like software. Typing the
price is the step that feels like answering a question.

**Question 2 — "Can the assistant give a discount?"** Three plain choices:

- **No** — `negotiable = false`.
- **A little** — the assistant may come down slightly; the floor is derived conservatively from the list
  price and the steps default. The owner states no secret number at all.
- **Down to ₹___** — the owner types their real lowest price. This is the only path where a floor is
  entered by hand, and the field stays write-only (INV-1).

**Everything else is derived, not asked:** concession steps, rounding (from the price's magnitude — whole
rupees on small items, tens or fifties on large ones), concession conditions (none by default), currency.

**Asked once, not per product.** At onboarding: "Can you usually give a discount?" That becomes the shop
default, so adding the second to the fiftieth product is a name and a price. A category-level change
applies to every product in it, which is how an owner thinks about a price revision anyway.

**The advanced section keeps every field**, unchanged, for the shop that genuinely prices in bands with
conditions. It is collapsed by default and nothing in the common path requires opening it.

## Consequences

- Adding a product goes from ten decisions to two, which is the difference between a catalog that gets
  filled in and one that does not. For a self-serve product sold to non-technical owners, this is an
  activation metric, not a polish item.
- Derived values are conservative by design, so a shop relying on the defaults negotiates less
  aggressively than one that configures fully. That is the right failure direction: the risk of a
  derived floor is a lost rupee, and the risk of a mishandled one is the owner's money.
- Inference needs to be forgiving of how people actually type — `450`, `Rs 450`, `450/-`, `450 to 600`.
  The existing money-input filtering (`lib/money.ts`, DEAL-118) already normalises digits; this extends
  it to the price shape and needs its own tests.
- Two ways to reach the same stored row means the editor must round-trip: a policy set in the advanced
  section has to render sensibly in the simple view or be clearly marked as advanced.
- Interacts with ADR 0006: on the **A little** path the floor is derived and already conservative, so
  where the final quote stops matters less. On the **Down to ₹___** path the owner stated a real
  minimum, and that is where stopping short of the floor protects something real.

## Implementation notes (2026-11-03)

- **Built for a product with one variant and nothing advanced** (the common case). The editor shows "What is the price?" (one typed field; `parsePrice` understands `450`, `Rs 450/-`, `₹1,250`, `from 450`, `450+`,
  `450-600`, `450 to 600`, "ask me") and "Can the assistant give a discount?" (No / A little / Down to a lowest price). Rules and tests: `frontend/src/features/catalog/simple.ts`, `simple.test.ts`.
- **Derived values:** steps = 2, rounding = whole rupees under ₹200, tens under ₹5,000, fifties above, no conditions. **"A little" = up to 5% off** (`LITTLE_DISCOUNT_PERCENT`), the private lowest price
  rounded up to the shop's rounding so it is never lower than promised and always below the price. The 5% is my choice under "derived conservatively"; the founder can change the one constant.
- **Advanced stays complete.** "More options" opens the editor as it was. A product the three answers cannot express (conditions, custom steps, bargaining that comes to the owner, "after a question or two"),
  and any product with several variants, opens in the full editor. A saved lowest price is write-only (INV-1), so a saved one reads back as "Down to a lowest price" and shows "Saved privately".
- **Ranges and "ask me"** show no discount question: a bargain needs a price to start from, so those use "More options".
- **Not built yet:** the shop-wide default asked once at onboarding ("Can you usually give a discount?") and the category-level change.
- Checked in a browser against a local stack: the typed price and the three answers, `starts from`, a range, the invalid-price message, and the derived "a little" needing no number
  (`e2e/pricing-safety.spec.ts`).

## How to revisit

Measure it: products per shop after onboarding, and how many shops ever open the advanced section. If
almost nobody opens it, consider moving those fields out of the product editor entirely into shop
settings. If owners frequently set a floor and then override it, the derived default is wrong and should
be tuned from the data rather than by argument.
