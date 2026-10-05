import re

from mcp_vision.identity import BUNDLE_ID


def test_bundle_id_is_reverse_dns_and_not_the_plip_dev_app():
    # macOS ties every permission to this; it must stay valid and never collide with plip.dev's own app.
    assert re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", BUNDLE_ID)
    assert BUNDLE_ID != "dev.plip.Plip"
