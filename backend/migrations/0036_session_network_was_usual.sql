-- Whether a console session started from the network of the founder's previous sign-in. Decided when the session starts: judged later against "the
-- last sign-in" it would be wrong, because by then the last sign-in is the session being judged (an attacker's own network would look usual).
ALTER TABLE admin_sessions ADD COLUMN usual_network boolean;
