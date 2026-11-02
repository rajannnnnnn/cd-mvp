-- 0019 — owner alerts become requests the gateway sends (ADR 0025). The agent records an alert as a queued owner message
-- and the gateway sends it, so the agent no longer chooses a number, decrypts a token or talks to Meta. The gateway needs
-- what the agent used to pass in memory: the template parameters and the language.
ALTER TABLE owner_messages
  ADD COLUMN params   jsonb NOT NULL DEFAULT '[]',
  ADD COLUMN language text;
