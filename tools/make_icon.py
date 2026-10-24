from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

SIZES = (16, 24, 32, 48, 64, 128, 256)


def render(size: int) -> QImage:
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
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError(f"Qt could not encode the {image.width()}px icon as PNG")
    buffer.close()
    return bytes(buffer.data())


def build_ico(frames: list[bytes], sizes: tuple[int, ...]) -> bytes:
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    directory = b""
    for size, payload in zip(sizes, frames):
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
