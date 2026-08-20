"""Generate the Inno Setup wizard artwork using the existing Qt build dependency.

Inno Setup's stock wizard is unmistakably a stock wizard: grey, wordless, and identical to
every other installer built the same way. These two images are the whole of what the setup
program shows about itself before anything is installed, so they carry the same colours as
the application icon rather than introducing a second look.

Both are drawn once against a logical canvas and rasterised at several sizes, because Inno
picks the closest match for the display's scaling and will otherwise stretch one bitmap
across a 4K screen. The output is BMP: PNG support arrived in Inno Setup 6.3, and BMP works
on every 6.x a contributor might have installed.

The files land in packaging/wizard/ and not in assets/, which is deliberate — the compiled
build sweeps up all of assets/, and installer chrome has no business inside the running
application.
"""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
)

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "packaging" / "wizard"

BACKDROP_TOP = QColor("#11151b")
BACKDROP_BOTTOM = QColor("#1e252f")
PANEL = QColor("#1e252f")
GOLD = QColor("#d5ad52")
GOLD_BRIGHT = QColor("#f0c862")
MUTED = QColor("#91a0b4")

# Inno Setup scales the wizard images by the display's DPI and picks the nearest supplied
# size. These are the sizes its own documentation lists for the two slots.
SIDEBAR_SIZES = ((164, 314), (192, 386), (246, 459), (328, 628), (410, 797))
BADGE_SIZES = ((55, 55), (64, 68), (92, 97), (110, 116), (138, 140), (164, 161))

SIDEBAR_CANVAS = (164.0, 314.0)
BADGE_CANVAS = (55.0, 55.0)


def _canvas(width: int, height: int, logical: tuple[float, float]) -> tuple[QImage, QPainter]:
    """An image of the requested pixel size, with the painter scaled to logical units."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(width / logical[0], height / logical[1])
    return image, painter


def _draw_mark(painter: QPainter, rect: QRectF, stroke: float, radius: float) -> None:
    """The application's own icon: a gold-edged panel with the initials inside it."""
    painter.setPen(QPen(GOLD, stroke))
    painter.setBrush(PANEL)
    painter.drawRoundedRect(rect, radius, radius)
    painter.setPen(GOLD_BRIGHT)
    font = QFont("Segoe UI", int(rect.height() * 0.42), QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "OT")


def _draw_sidebar(painter: QPainter) -> None:
    width, height = SIDEBAR_CANVAS
    gradient = QLinearGradient(0.0, 0.0, 0.0, height)
    gradient.setColorAt(0.0, BACKDROP_TOP)
    gradient.setColorAt(1.0, BACKDROP_BOTTOM)
    painter.fillRect(QRectF(0.0, 0.0, width, height), gradient)

    _draw_mark(painter, QRectF(42.0, 54.0, 80.0, 80.0), 3.0, 16.0)

    painter.setPen(GOLD_BRIGHT)
    painter.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
    painter.drawText(
        QRectF(0.0, 152.0, width, 24.0), Qt.AlignmentFlag.AlignCenter, "OSRS Toolkit"
    )

    painter.setPen(QPen(GOLD, 1.0))
    painter.drawLine(int(width / 2 - 28), 182, int(width / 2 + 28), 182)

    painter.setPen(MUTED)
    painter.setFont(QFont("Segoe UI", 8))
    painter.drawText(
        QRectF(18.0, 194.0, width - 36.0, 60.0),
        int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap),
        "Market analysis and trade tracking for Old School RuneScape",
    )


def _draw_badge(painter: QPainter) -> None:
    width, height = BADGE_CANVAS
    painter.fillRect(QRectF(0.0, 0.0, width, height), BACKDROP_TOP)
    _draw_mark(painter, QRectF(4.0, 4.0, width - 8.0, height - 8.0), 2.0, 9.0)


def _render(name: str, sizes: tuple[tuple[int, int], ...], logical, draw) -> list[Path]:
    written: list[Path] = []
    for width, height in sizes:
        image, painter = _canvas(width, height, logical)
        draw(painter)
        painter.end()
        # Inno's own naming: the base size is unsuffixed, larger ones carry @2x-style
        # markers only by convention, so plain dimensions keep the .iss list readable.
        path = OUTPUT_DIR / f"{name}-{width}x{height}.bmp"
        if not image.save(str(path), "BMP"):
            raise RuntimeError(f"Qt could not write {path}")
        written.append(path)
    return written


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    written = _render("sidebar", SIDEBAR_SIZES, SIDEBAR_CANVAS, _draw_sidebar)
    written += _render("badge", BADGE_SIZES, BADGE_CANVAS, _draw_badge)
    for path in written:
        print(f"Wrote {path.relative_to(OUTPUT_DIR.parents[1])}")
    app.quit()


if __name__ == "__main__":
    main()
