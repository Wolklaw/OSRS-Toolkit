from __future__ import annotations

import struct
from pathlib import Path

import pytest

ICON = Path(__file__).resolve().parents[1] / "assets" / "osrs_toolkit.ico"

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

REQUIRED = {16, 32, 48, 256}


def read_directory() -> list[dict]:
    raw = ICON.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    assert reserved == 0, "not an icon file"
    assert kind == 1, f"expected an icon, got resource type {kind}"
    entries = []
    offset = 6
    for _ in range(count):
        width, _h, _cc, _r, _planes, bpp, size, data_at = struct.unpack(
            "<BBBBHHII", raw[offset : offset + 16]
        )
        offset += 16
        entries.append(
            {
                "size": width or 256,
                "bpp": bpp,
                "png": raw[data_at : data_at + 8] == PNG_MAGIC,
                "bytes": size,
            }
        )
    return entries


def test_the_icon_carries_the_sizes_windows_asks_for():
    present = {entry["size"] for entry in read_directory()}

    missing = REQUIRED - present
    assert not missing, (
        f"the icon has no {sorted(missing)}px image, so Windows has nothing to draw at "
        f"taskbar size and falls back to a blank square. Present: {sorted(present)}"
    )


@pytest.mark.parametrize("entry", read_directory(), ids=lambda e: f"{e['size']}px")
def test_every_image_is_png_and_full_colour(entry: dict):
    assert entry["png"], f"the {entry['size']}px image is not PNG-compressed"
    assert entry["bpp"] == 32, f"the {entry['size']}px image is {entry['bpp']}bpp, not 32"


def test_the_icon_did_not_balloon_back_into_one_uncompressed_bitmap():
    assert ICON.stat().st_size < 100_000, (
        "the icon is large enough to be a single uncompressed bitmap again"
    )
