from pathlib import Path

from PIL import Image

DMG = Path(__file__).resolve().parents[1] / "assets" / "brand" / "dmg"


def test_dmg_background_has_a_1x_and_a_2x_at_the_window_size():
    # build_dmg joins these with tiffutil -cathidpicheck, which needs exactly 1x and 2x of the 660x440 window
    with Image.open(DMG / "background.png") as one, Image.open(DMG / "background@2x.png") as two:
        assert one.size == (660, 440)
        assert two.size == (1320, 880)
