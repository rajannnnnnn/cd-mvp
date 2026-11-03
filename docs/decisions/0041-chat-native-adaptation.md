# 0041 The AI talks the way the customer talks: chat-native, never enterprise

**Status:** accepted · 2026-11-03 · founder: "never make it enterprise, it is chat: from highly casual to professional; it is WhatsApp. Adapting to the user's way is important to me: language, way of speaking, all"

## Context

The product's goal is more sales from WhatsApp chats, and the first thing a small-shop customer feels is whether the reply sounds like a person
in their conversation or like a company. Reading the code against that goal found three gaps:

1. The only instruction was one line in the writer prompt ("mirror language, script and formality").
2. The learned customer style existed as a column (`customers.profile`) that **nothing ever wrote**, so the planner received an empty style, and the
   writer received only language and script, not register, length, emoji or how the customer addresses the shop.
3. Nothing stopped a reply from sounding like a support desk ("Dear customer, kindly be informed...") or arriving formatted like a document.

## Options

1. **A better prompt only.** Cheap, but unmeasured and silent when the model ignores it.
2. **Ask the model to describe the customer's style first.** One more model call per turn, for something rules can see.
3. **Rules describe the style; the writer is told it and held to it; a deterministic check enforces the floor.**

## Decision

Option 3.

- **Style by rules, from the customer's own messages** (`modules/agent/style.py`): register from very casual to formal (slang and shorthand, emoji,
  lowercase-with-no-punctuation habit, repeated letters, against "kindly", "sir/madam", "could you please", proper sentences), language and script mix
  per message, typical length, emoji use, punctuation habit, and how they address the shop (bhai, sir, ji). Recomputed from their most recent messages
  (twelve, recent weighted more), so a customer who changes tone is followed. No model call, no cost, one customer only: a profile is built from one
  customer's messages and used for that customer's reply, never carried to anyone else. It is also kept on the customer record.
- **The writer is told** (`customer_style`, `reply_hint`: about as long as they write, one message, an emoji only for someone who uses them). The writer
  prompt (v4) is rewritten for chat: match their register from very casual to professional, keep their language mix and their words, never write like a
  company, no headers, bullets or double-asterisk bold. The prompt says the customer's own register wins over the owner's voice examples when they differ,
  and that none of this ever bends the hard rules on numbers and floors.
- **A deterministic voice check** runs on every reply with the other checks (`check_voice`): stock support-desk phrases, document formatting, a string of
  emoji or any emoji for a professional customer who uses none, and a reply far longer than the customer writes are sent back to the writer with the reason
  (one rewrite, then the existing safe holding message).
- **Evaluation:** five new scenarios (very casual Hinglish with emoji, a formal customer with no emoji, one-word messages, Devanagari, an ordinary customer)
  and an invariant, on every scenario, that no reply sounds like a company. They run on the local stand-in on every build and on a real model whenever one
  is configured.

## Consequences

- Replies fit the person at no extra model cost; the check is the floor under the prompt's intent.
- The rules are a guess from visible habits of writing, not a judgement of the person; a wrong guess costs a slightly off tone, never a wrong fact.
- The local stand-in writer cannot truly mirror a customer; whether a real model does is only measurable with a key. That run is on the go-live checklist.

## How to revisit

If real chats show the register labels misjudging a group of customers, adjust the signals in `style.py` first (it is a few word lists and weights, with its own
tests). If a model needs more than the profile to match a voice, add the customer's last few messages verbatim as examples. Tamil, Tanglish and other
languages join through the same `language` and `register` fields once they are detected.
