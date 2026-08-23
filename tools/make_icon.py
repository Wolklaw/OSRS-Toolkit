"""Generate the Windows application icon using the existing Qt build dependency.

Windows asks for this icon at several sizes and picks the closest one it is given. A single
256x256 entry is not "one size that scales" — the taskbar, Alt-Tab and Explorer's smaller
views want something around 16-48px, and when the only thing on offer is a 256x256 stored as
an uncompressed BMP, they render nothing at all. That is what shipped: a real icon on the
executable that the taskbar drew as a blank square.

So every size is drawn at its own scale rather than resampled down from one big one, and each
is stored as PNG, which is what the Vista-era icon format expects for anything this size.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

#: What Windows actually reaches for: 16 in the taskbar's small view and Explorer's list, 32
#: for the standard shell, 48 for medium icons, and 256 for the preview pane. The ones between
#: are cheap and stop Windows resampling across a wide gap.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    """The mark at one size, drawn at that size.

    Proportional rather than fixed so the border stays a border instead of thinning to nothing:
    at 16px the 12px stroke of the full-size drawing rounds to under a pixel and disappears.
    """
    scale = size / 256
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor("#11151b"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    stroke = max(1.0, 12 * scale)
    painter.setPen(QPen(QColor("#d5ad52"), stroke))
    painter.setBrush(QColor("#1e252f"))
    inset = 14 * scale
    painter.drawRoundedRect(
        inset, inset, size - 2 * inset, size - 2 * inset, 42 * scale, 42 * scale
    )
    painter.setPen(QColor("#f0c862"))
    font = QFont("Segoe UI", max(1, round(58 * scale)), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "OT")
    painter.end()
    return image


def as_png(image: QImage) -> bytes:
    # QBuffer with no argument owns its storage. Handing it a QByteArray built inline instead
    # hands it one that Python is free to collect while Qt still holds the pointer, which
    # segfaults rather than failing.
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Qt could not encode the {image.width()}px icon as PNG")
    buffer.close()
    return bytes(buffer.data())


def build_ico(frames: list[bytes], sizes: tuple[int, ...]) -> bytes:
    """Assemble the container by hand.

    Qt writes one image per .ico and offers no way to add a second, which is the whole reason
    the old icon had a single size in it. The format itself is a six-byte header, one sixteen-
    byte directory entry per image, then the payloads — cheaper to write than to take on an
    imaging dependency for.
    """
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    directory = b""
    for size, payload in zip(sizes, frames):
        # 256 is stored as 0: the field is one byte and the format predates the larger size.
        directory += struct.pack(
            "<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(payload), offset
        )
        offset += len(payload)
    return header + directory + b"".join(frames)


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    output = Path(__file__).resolve().parents[1] / "assets" / "osrs_toolkit.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    frames = [as_png(render(size)) for size in SIZES]
    output.write_bytes(build_ico(frames, SIZES))
    app.quit()


if __name__ == "__main__":
    main()
