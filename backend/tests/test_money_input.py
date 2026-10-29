"""DEAL-118: a price is a plain number, from the screen, the API and the database. Nothing a person could mistype or paste gets in."""
from __future__ import annotations

import copy
import re
from decimal import Decimal

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tests.test_api import PRODUCT, V1, hdr, owner

PLAIN = re.compile(r"^[0-9]{1,10}(\.[0-9]{1,2})?$")


def product(price) -> dict:
    p = copy.deepcopy(PRODUCT)
    p["variants"][0]["policy"].update({"list_price": price, "negotiable": False, "ai_may_negotiate": False, "concession_steps": 0})
    p["variants"][0]["policy"].pop("floor_price", None)
    return p


@pytest.mark.parametrize("price", ["1200", "1200.5", "1200.50", "0.01", 1200, 1200.5, "9999999999.99"])
async def test_plain_numbers_are_saved_exactly(world, client, price):
    _, tok = await owner(world, client)
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=product(price))
    assert r.status_code == 201, r.text
    assert Decimal(r.json()["variants"][0]["policy"]["list_price"]) == Decimal(str(price))


@pytest.mark.parametrize("price", ["abc", "12abc", "1,200", "1e3", "0x10", " 12", "12 ", "+5", "-5", "0", "0.00", "NaN", "Infinity", "1200.555",
                                   "12345678901", "", ".5", "5.", "₹500", "१२३", "1 200", True])
async def test_anything_that_is_not_a_plain_number_is_refused_with_a_message_naming_the_field(world, client, price):
    _, tok = await owner(world, client)
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=product(price))
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["details"][0]["field"] == "variants.0.policy.list_price"
    assert err["message"].startswith("Price "), err["message"]            # names the field, not "Some fields are invalid."
    assert (await client.get(f"{V1}/products", headers=hdr(tok))).json()["total"] == 0     # and nothing was saved


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(text=st.text(alphabet=st.sampled_from(list("0123456789.,-+eE xX₹abc")), min_size=0, max_size=14))
async def test_random_text_is_either_refused_or_saved_exactly_as_typed(world, client, text):
    if not hasattr(world, "_money_tok"):                    # one sign-in for all examples (the fixture lives across them)
        world._money_tok = (await owner(world, client))[1]
    tok = world._money_tok
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=product(text))
    if PLAIN.match(text) and Decimal(text) > 0:
        assert r.status_code == 201 and Decimal(r.json()["variants"][0]["policy"]["list_price"]) == Decimal(text)
    else:
        assert r.status_code == 422


async def test_every_price_field_is_strict_not_only_the_list_price(world, client):
    _, tok = await owner(world, client)
    p = product("1000")
    p["variants"][0]["policy"].update({"disclosure": "range", "range_min": "abc", "range_max": "2000"})
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=p)
    assert r.status_code == 422 and r.json()["error"]["details"][0]["field"].endswith("range_min")
    p = copy.deepcopy(PRODUCT)
    p["variants"][0]["policy"]["floor_price"] = "8e2"
    assert (await client.post(f"{V1}/products", headers=hdr(tok), json=p)).status_code == 422
    assert (await client.post(f"{V1}/offers", headers=hdr(tok), json={"name": "x", "kind": "flat", "value": "20o"})).status_code == 422
    assert (await client.post(f"{V1}/offers", headers=hdr(tok), json={"name": "x", "kind": "percent", "value": "0"})).status_code == 422
    r = await client.post(f"{V1}/pricing/ladder", headers=hdr(tok), json={"list_price": "1e3", "floor_price": "800", "concession_steps": 3})
    assert r.status_code == 422
    ok = await client.post(f"{V1}/pricing/ladder", headers=hdr(tok), json={"list_price": "1000", "floor_price": "800", "concession_steps": 3})
    assert ok.status_code == 200


async def test_several_mistakes_are_listed_together(world, client):
    _, tok = await owner(world, client)
    p = product("abc")
    p["variants"][0]["stock_qty"] = -1
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=p)
    err = r.json()["error"]
    assert r.status_code == 422 and len(err["details"]) == 2 and err["message"].startswith("Please fix: ") and "Price must be a number" in err["message"] and "Stock count" in err["message"]


async def test_the_database_refuses_a_zero_price_even_if_the_application_did_not(world, client, pg_urls):
    _, tok = await owner(world, client)
    r = await client.post(f"{V1}/products", headers=hdr(tok), json=product("500"))
    vid = r.json()["variants"][0]["id"]
    with psycopg.connect(pg_urls["super"], autocommit=True) as c:
        for sql in ("UPDATE pricing_policies SET list_price = 0 WHERE variant_id = %s", "UPDATE pricing_policies SET range_min = 0 WHERE variant_id = %s"):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(sql, (vid,))
