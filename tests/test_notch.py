from mcp_vision.notch import top_center_origin


def test_notch_centers_horizontally_near_the_top():
    x, y = top_center_origin((0, 0, 1440, 900), (460, 62))
    assert x == (1440 - 460) / 2
    assert y == 12


def test_notch_origin_respects_screen_offset():
    x, _y = top_center_origin((100, -900, 1200, 800), (400, 88))
    assert x == 100 + (1200 - 400) / 2
