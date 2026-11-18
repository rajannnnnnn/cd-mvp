"""QA should not have to set the simulator up again after a restart: the phones, shop numbers, chats and behaviour settings are kept in a file."""
from __future__ import annotations

from dataclasses import asdict

from metasim.app import Biz, Msg, Sim


def _sim(path) -> Sim:
    s = Sim()
    s.state_file = path
    return s


def test_phones_numbers_chats_and_behaviour_come_back_after_a_restart(tmp_path):
    f = tmp_path / "state.json"
    a = _sim(f)
    a.biz["sim-pn-9"] = Biz("sim-pn-9", "sim-waba-9", "+919700000002", "Vetri Tyres", "SIMTOKEN-9")
    a.customers["919800001001"] = "919800001001"
    a.msgs.append(Msg("m1", "sim-pn-9", "919800001001", "in", "text", "hey", 1000.0, marks={"read": 1002.5}))
    a.last_customer_msg[("sim-pn-9", "919800001001")] = 1000.0
    a.settings.phone_offline = True
    a.settings.app_secret = "from-the-environment"
    a.save_state()

    b = _sim(f)
    assert b.load_state() is True
    assert b.biz["sim-pn-9"].display == "+919700000002" and b.biz["sim-pn-9"].name == "Vetri Tyres"
    assert "919800001001" in b.customers
    assert [asdict(m) for m in b.msgs] == [asdict(m) for m in a.msgs] and b.msgs[0].marks == {"read": 1002.5}
    assert b.last_customer_msg == {("sim-pn-9", "919800001001"): 1000.0}
    assert b.settings.phone_offline is True


def test_secrets_are_never_written_to_the_file_and_the_environment_still_wins(tmp_path):
    f = tmp_path / "state.json"
    a = _sim(f)
    a.settings.app_secret, a.settings.verify_token = "super-secret-app", "super-secret-verify"
    a.save_state()
    text = f.read_text(encoding="utf-8")
    assert "super-secret-app" not in text and "super-secret-verify" not in text
    b = _sim(f)
    b.settings.app_secret = "set-by-the-environment"
    b.load_state()
    assert b.settings.app_secret == "set-by-the-environment"


def test_a_missing_or_unreadable_file_starts_clean_instead_of_failing(tmp_path):
    f = tmp_path / "state.json"
    assert _sim(f).load_state() is False                       # nothing saved yet
    f.write_text("{ not json", encoding="utf-8")
    s = _sim(f)
    assert s.load_state() is False and s.biz == {}             # garbage: ignored
    f.write_text('{"version": 1, "biz": [{"pnid": "x"}], "customers": {}, "msgs": []}', encoding="utf-8")
    assert _sim(f).load_state() is False                        # a record with missing fields: ignored, not half-loaded


def test_changes_are_saved_after_publishing_an_event(tmp_path):
    f = tmp_path / "state.json"
    s = _sim(f)
    s.customers["919800009999"] = "919800009999"
    s.publish("customers", s.customers)                         # no running loop in a plain test: saved at once
    assert f.exists() and "919800009999" in f.read_text(encoding="utf-8")
    g = tmp_path / "other.json"
    t = _sim(g)
    t.publish("wire", {"x": 1})                                 # events that change nothing worth keeping do not write
    assert not g.exists()
