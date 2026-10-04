# 0015 Voice-note transcription deferred by the founder; the seam is kept

**Status:** accepted · 2026-10-04 · **overrides the milestone order (M10) on the founder's instruction** ("no voice for now")

## Decision
Voice notes are not transcribed in this build. Inbound audio is stored, shown in the chat as a voice message, and the
assistant answers that it cannot play voice notes yet and asks the customer to type (never silence, INV-12). The
marketing site and UI mark voice as "coming soon". The conversation flow already handles `kind="audio"` messages, and the
Technical Design's `Transcriber` port is the single place to add a vendor: transcribe before the turn is decided.

## How to revisit
When the founder wants voice: pick a transcriber by testing Hindi/English/Hinglish accuracy on recorded audio, implement
the port, add the step to the turn flow, add scenarios to the eval suite.
