"""What a console sign-in came from, in words (ADR 0048). Pure functions."""
from __future__ import annotations

import pytest

from salesai.modules.auth.device import (
    clean_geo,
    describe_device,
    is_private,
    network_of,
    where_label,
)


@pytest.mark.parametrize("ua, expected", [
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36", "Chrome 142 on Windows"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36 Edg/141.0.0.0", "Edge 141 on Windows"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0", "Firefox 130 on Linux"),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1", "Safari 17 on iPhone"),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15", "Safari 17 on macOS"),
    ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36", "Chrome 142 on Android"),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36 OPR/120.0.0.0", "Opera 120 on Windows"),
    ("curl/8.5.0", "curl 8.5.0"),
    ("python-httpx/0.28.1", "Python 0.28.1"),
    ("", "Unknown device"),
    (None, "Unknown device"),
    ("something odd", "Unknown device"),
    ("Mozilla/5.0 (X11; Linux x86_64)", "Unknown browser on Linux"),
])
def test_a_browser_is_described_in_words(ua, expected):
    assert describe_device(ua) == expected


def test_the_description_never_repeats_the_raw_text_or_leaks_more_than_browser_and_system():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; secret-token-123) AppleWebKit/537.36 Chrome/142.0.0.0 Safari/537.36"
    assert "secret" not in describe_device(ua)


def test_where_is_the_address_with_a_country_when_known_and_a_note_for_private_networks():
    assert where_label("203.0.113.9", "IN") == "IN · 203.0.113.9"
    assert where_label("203.0.113.9", None) == "203.0.113.9"
    assert where_label("172.18.0.1", None) == "Private network · 172.18.0.1"
    assert where_label(None, None) == "Unknown" and where_label(None, "IN") == "Unknown"
    assert is_private("10.0.0.5") and not is_private("203.0.113.9") and not is_private("garbage") and not is_private(None)


def test_a_country_header_is_trusted_only_if_it_looks_like_a_country():
    assert clean_geo("IN") == "IN" and clean_geo(" in ") == "IN"
    for junk in (None, "", "<script>", "India", "I", "XX", "T1", "1N", "IN; DROP"):
        assert clean_geo(junk) is None


def test_networks_are_neighbourhoods_not_exact_addresses():
    assert network_of("203.0.113.10") == network_of("203.0.113.250") == "203.0.113.0/24"
    assert network_of("2001:db8:1:2::1") == network_of("2001:db8:1:ffff::9") != network_of("2001:db8:2::1")
    assert network_of(None) is None and network_of("not an address") is None
