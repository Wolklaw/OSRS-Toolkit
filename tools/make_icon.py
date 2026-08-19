"""Generate the Windows application icon using the existing Qt build dependency."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen


def main() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    output = Path(__file__).resolve().parents[1] / "assets" / "osrs_toolkit.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(QColor("#11151b"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor("#d5ad52"), 12))
    painter.setBrush(QColor("#1e252f"))
    painter.drawRoundedRect(14, 14, 228, 228, 42, 42)
    painter.setPen(QColor("#f0c862"))
    painter.setFont(QFont("Segoe UI", 58, QFont.Weight.Bold))
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "OT")
    painter.end()
    if not image.save(str(output), "ICO"):
        raise RuntimeError("Qt could not write the Windows icon")
    app.quit()


if __name__ == "__main__":
    main()
