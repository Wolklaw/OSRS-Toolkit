"""Generate the 1280x640 GitHub social preview card (Settings > General > Social preview).

Crops the GE Flipper table and leaves the sidebar out, keeping the busiest part of the app
in frame without showing nav contents that go stale between releases.
"""

from pathlib import Path

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
)

WIDTH, HEIGHT = 1280, 640
BACKGROUND = QColor("#0d1117")
PANEL = QColor("#161c26")
GOLD = QColor("#d5ad52")
TEXT = QColor("#e6edf3")
MUTED = QColor("#8b98a5")

STRIP_TOP = 312
TABLE_LEFT = 257  # First pixel right of the sidebar in ge-flipper.png.
TABLE_WIDTH = 1618
TABLE_HEADER_TOP = 318


def main() -> int:
    # Held for the lifetime of main(): QImage text rendering needs a live application.
    _app = QGuiApplication.instance() or QGuiApplication([])
    root = Path(__file__).resolve().parents[1]

    card = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
    card.fill(BACKGROUND)
    painter = QPainter(card)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    strip_height = HEIGHT - STRIP_TOP
    scale = WIDTH / TABLE_WIDTH
    screenshot = QImage(str(root / "docs" / "images" / "ge-flipper.png"))
    if screenshot.isNull():
        raise SystemExit("docs/images/ge-flipper.png is missing")
    source = screenshot.copy(
        QRect(TABLE_LEFT, TABLE_HEADER_TOP, TABLE_WIDTH, int(strip_height / scale))
    )
    painter.drawImage(QRectF(0, STRIP_TOP, WIDTH, strip_height), source)

    # Blend the strip into the background instead of butting it against a hard edge.
    top_fade = QLinearGradient(0, STRIP_TOP, 0, STRIP_TOP + 90)
    top_fade.setColorAt(0.0, BACKGROUND)
    top_fade.setColorAt(1.0, QColor(13, 17, 23, 0))
    painter.fillRect(QRectF(0, STRIP_TOP, WIDTH, 90), top_fade)

    # Dissolve the final row rather than slicing it in half.
    bottom_fade = QLinearGradient(0, HEIGHT - 78, 0, HEIGHT)
    bottom_fade.setColorAt(0.0, QColor(13, 17, 23, 0))
    bottom_fade.setColorAt(1.0, BACKGROUND)
    painter.fillRect(QRectF(0, HEIGHT - 78, WIDTH, 78), bottom_fade)

    icon = QImage(str(root / "assets" / "osrs_toolkit.ico"))
    painter.setPen(QPen(GOLD, 3))
    painter.setBrush(PANEL)
    painter.drawRoundedRect(QRectF(72, 64, 92, 92), 18, 18)
    if not icon.isNull():
        painter.drawImage(QRectF(84, 76, 68, 68), icon)

    painter.setFont(QFont("Segoe UI", 40, QFont.Weight.Bold))
    painter.setPen(TEXT)
    painter.drawText(
        QRectF(192, 62, 900, 62),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "OSRS Toolkit",
    )

    wordmark = QFont("Segoe UI", 15)
    wordmark.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
    painter.setFont(wordmark)
    painter.setPen(GOLD)
    painter.drawText(
        QRectF(196, 120, 900, 30),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "MARKET COMPANION",
    )

    painter.setFont(QFont("Segoe UI", 22))
    painter.setPen(TEXT)
    painter.drawText(
        QRectF(72, 190, 1140, 36),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "Plan better trades. Record what actually happened.",
    )

    painter.setFont(QFont("Segoe UI", 15))
    painter.setPen(MUTED)
    painter.drawText(
        QRectF(72, 238, 1140, 28),
        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        "Free Windows app  ·  No login  ·  No game automation  ·  Your journal stays on your PC",
    )

    painter.end()
    output = root / "docs" / "images" / "social-preview.png"
    if not card.save(str(output), "PNG"):
        raise SystemExit(f"Could not save {output}")
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
