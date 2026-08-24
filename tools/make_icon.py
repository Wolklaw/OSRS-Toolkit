"""Generate the Windows application icon (multi-size .ico) using the existing Qt build
dependency.

Windows picks the closest available size from the .ico, so each size is drawn at its own
scale rather than resampled down from one big image — a single 256x256 entry renders as a
blank square in the taskbar and other small views.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

# Sizes Windows actually uses: 16 (taskbar/Explorer list), 32 (shell), 48 (medium icons),
# 256 (preview pane); the rest fill gaps to avoid wide resampling jumps.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
    """The mark at one size, drawn at that size (proportional stroke so it doesn't vanish
    at small sizes)."""
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
    # QBuffer() owns its storage; a QByteArray built inline could be GC'd while Qt still
    # holds the pointer, causing a segfault.
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Qt could not encode the {image.width()}px icon as PNG")
    buffer.close()
    return bytes(buffer.data())


def build_ico(frames: list[bytes], sizes: tuple[int, ...]) -> bytes:
    """Assemble the .ico container by hand, since Qt can only write one size per .ico.
    Format: six-byte header, one sixteen-byte directory entry per image, then payloads."""
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    directory = b""
    for size, payload in zip(sizes, frames):
        # 256 is stored as 0 — the field is one byte and predates the larger size.
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
