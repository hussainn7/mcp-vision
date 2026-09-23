import time

from mcp_vision.partial_intent import PartialIntentWatcher, preparation_for


def test_flight_prefix_gets_safe_preparation_label():
    assert preparation_for("find flights") == {"kind": "flights", "label": "Finding flights…"}
    assert preparation_for("find flights to san francisco")["kind"] == "flights"


def test_open_app_prefix_prepares_label_without_launching():
    prep = preparation_for("open calculator")
    assert prep is not None and prep["kind"] == "open_app" and "Calculator" in prep["label"]


def test_plain_words_prepare_nothing():
    assert preparation_for("hello there my friend") is None
    assert preparation_for("") is None


def test_watcher_fires_once_after_stability():
    watcher = PartialIntentWatcher(min_gap=0)
    assert watcher.observe("find flights") is None
    assert watcher.observe("find flights to san")["kind"] == "flights"
    assert watcher.observe("find flights to san francisco") is None
    watcher.reset()
    assert watcher.observe("find flights") is None


def test_watcher_requires_second_observation():
    watcher = PartialIntentWatcher(min_gap=60)
    assert watcher.observe("find flights") is None
    assert watcher.observe("find flights to sf") is None
