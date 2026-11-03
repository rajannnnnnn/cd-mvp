-- 0030 — owner messages may now carry a usage warning (ADR 0032). The list is the one in 0001 plus 'usage_warning'; 'daily_summary' stays
-- allowed only so that rows written before the daily summary was removed (ADR 0036 note, FR-RP-1 struck) remain valid.
ALTER TABLE owner_messages DROP CONSTRAINT owner_messages_purpose_check;
ALTER TABLE owner_messages ADD CONSTRAINT owner_messages_purpose_check
  CHECK (purpose IN ('chat','handoff_alert','daily_summary','deal_alert','disconnect_alert','usage_warning','setup','otp'));
