"""Flights: never nag for dates the user already deferred, and always hand
Google Flights a concrete origin/destination + date range so offer cards render.
"""
from __future__ import annotations

import re

from datetime import date

from mcp_vision.plan import _flight_term, _relative_flight_dates
from mcp_vision.request_routing import route_request


def test_any_dates_is_a_valid_choice_not_a_question():
    r = route_request("find flights to SFO from Atlanta, pick any dates you want", "ask")
    assert r.kind == "browser"


def test_flexible_and_whenever_also_satisfy_dates():
    for req in ("flights from Atlanta to Chicago whenever",
                "flights to Paris from New York, flexible",
                "bookable flights from Atlanta to Miami any time"):
        assert route_request(req, "ask").kind == "browser"


def test_missing_origin_is_still_asked():
    r = route_request("flights to SFO any dates", "ask")
    assert r.kind == "input" and r.missing == "departure"


def test_concrete_dates_keep_both_route_and_dates():
    term = _flight_term("flights from ATL to SFO September 28 to October 2")
    assert "September 28 to October 2" in term


def test_deferred_dates_gain_concrete_offers_either_word_order():
    term = _flight_term("find flights to SFO from Atlanta, any dates")
    assert "Sep" in term and "to" in term
    assert "Atlanta" in term and "SFO" in term
    # city-before-to phrasing also gets a concrete date range
    assert "Sep" in _flight_term("round trip Atlanta to San Francisco")


def test_deferral_words_are_not_glued_to_city_names():
    term = _flight_term("flights to Paris from New York whenever")
    assert "whenever" not in term
    assert "New York" in term and "Paris" in term


def test_next_week_becomes_a_concrete_search_window():
    start, end, _ = _relative_flight_dates('flights next week', date(2026, 9, 16))
    assert start.isoformat() == '2026-09-21'
    assert end.isoformat() == '2026-09-27'
