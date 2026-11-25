# 0051 Language-neutral turn detection and replies, and the hand-over message

**Status:** accepted · 2026-11-24 · founder: "don't only focus on Hinglish as Indian languages, there are many, so it is the job of the model, not any code"; "language neutral does not mean normalize, answer in the user's language"; "the hard-coded message should not be generic: 'please hold on, I am connecting you to <the person>', sir or madam, or the owner if the name is not known"

## Context
Customers write in every Indian language and in English, often mixed. The code had Hindi and Hinglish built in at four places: the end-of-turn rules (word lists of Hinglish sentence endings), the planner's `language`/`script` fields (only `en | hi | hinglish`, `latin | devanagari`), the style profile (a Hinglish word list to guess the language) and the reply checks (Devanagari-or-Latin only; "I am an assistant" and "I am a person" patterns in English and Hindi). A Tamil or Bengali customer could be answered in English, or have a correct reply rejected by the checks and receive the holding message. The hard-coded message sent when the model fails was one English sentence ("I'm checking with the owner...") for everyone.

## Decisions
1. **End-of-turn rules use only what means the same in every language** (`conversations/eot.py`): the kind of message, how it ends (a sentence-final or continuing mark of any script), how long it is (in characters for scripts written without spaces), whether the AI just asked a question, and this customer's own typing rhythm. No word list of any language remains (a test fails if one returns). A short message with no ending mark ("unsure") now waits 1.5 s longer than a certain one, because no rule can know it is finished; the model reads meaning, not the rules.
2. **Language and script are free text from the planner**, sanitised, with a safe default so a garbled label never fails a turn. The writer is told to answer in the language and script the customer writes, not to translate or tidy, and to mix the way the customer mixes. Prompts are new versions (planner@3, writer@5, checker@3); the old versions stay.
3. **The script check works for any script** by Unicode script name of each letter. A script it does not list is not judged. Brand and product names in Latin inside a sentence in another script pass.
4. **The English/Hindi word patterns (price words, pressure, "I am a person", "I am an assistant") only judge English and Hindi.** For any other language the pipeline always runs the model check, and the checker prompt now carries the identity question (`must_disclose` → `identity_ok`) and pressure claims in any language. Numbers stay checked everywhere (digits are digits).
5. **The style profile describes the script (from Unicode), not a guessed language**, and counts words in any script.
6. **The hand-over message** (`agent/holding.py`): one message naming who the customer is being connected to: "Rajesh sir", "Priya madam", the name alone when no title is set, "the owner" when no name is known. The title is the owner's own setting (`sales_settings.owner_title`: `sir | madam`); it is never guessed from a name. Language: the language the planner last named for the chat (`qualification.last_language`), else the script of the customer's message, else English. Texts exist for English, Hinglish, Hindi, Marathi, Tamil, Telugu, Bengali, Gujarati, Kannada, Malayalam, Punjabi and Urdu, worded without a gender for the assistant.

## Measured (the labelled corpus, 61 bursts including Tamil, Bengali, Arabic, Marathi, Chinese and Telugu; ADR 0036 method)
| | answered before the customer finished | median wait after the last message | p95 | mean |
|---|---|---|---|---|
| first heuristic (v1) | 13.1 % | 1.6 s | 5.2 s | 3.3 s |
| rules with word lists (ADR 0036) | under 6 % (gate) | under 1.5 s (gate) | under 3.5 s | under 2.0 s |
| **language-neutral rules (this ADR)** | **4.9 %** | **2.7 s** | **3.2 s** | **2.0 s** |
Without word lists the median wait on a short message with no punctuation rises by about 1.2 s. That is the price of not special-casing languages. It is the trade to look at: the way to win the time back without a word list is speculative planning (start the model while waiting; the planner says whether the customer seems finished) from `docs/PRODUCTION_READINESS.md` section 5.

## Not done (said plainly)
- The style profile's register hints (slang, formal words) and the reply voice checks still use English and Hinglish word lists. They only nudge tone and never block a language, but they are language-specific code.
- Price words in the number check ("lakh", "hazar") and the pressure patterns know English and Hindi; other languages rely on digits and the model check.
- Owner-facing messages (alerts, summaries) are English and Hindi.
- The Hindi-in-Latin holding message is used when the chat was last named `hinglish`; a customer who has not been answered yet and writes Latin letters gets English.
- The holding texts in languages other than English, Hindi and Hinglish need a native speaker's read before launch.
- No shop-app screen sets `owner_title` yet (the API accepts it).

## How to revisit
Raise or lower `UNSURE_EXTRA` in `eot.py` (a share of the shop's own quiet time, so a shop that waits less is not forced to wait longer) and re-run `tests/test_eot_accuracy.py`. A language's hand-over text is one line in `holding.py`.
