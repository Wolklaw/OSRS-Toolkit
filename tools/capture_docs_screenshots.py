from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QTabWidget

from osrs_toolkit.app import MainWindow, SettingsDialog
from osrs_toolkit.market import WikiMarketClient


def save_widget(widget: object, path: Path, app: QApplication) -> None:
    app.processEvents()
    pixmap = widget.grab()  # type: ignore[attr-defined]
    if not pixmap.save(str(path), "PNG"):
        raise RuntimeError(f"Could not save {path}")


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    output_dir = project_root / "docs" / "images"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir = Path(tempfile.mkdtemp(prefix="osrs-toolkit-docs-"))
    os.environ["LOCALAPPDATA"] = str(profile_dir)

    app = QApplication([])
    app.setOrganizationName("OSRS Toolkit Screenshot")
    app.setApplicationName("OSRS Toolkit Screenshot")
    app.setFont(QFont("Segoe UI", 10))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(profile_dir))

    try:
        with patch("osrs_toolkit.app.QTimer.singleShot"):
            window = MainWindow()
        window.resize(1440, 900)
        window.show()
        mappings, points = WikiMarketClient().fetch_snapshot()
        window.cash.setValue(2_000_000)
        window._market_loaded(mappings, points, False)
        save_widget(window, output_dir / "ge-flipper.png", app)

        first = window._flips[0]
        position_id = window._journal.track(
            first.item_id,
            first.name,
            first.suggested_quantity,
            first.buy_price,
            first.sell_price,
        )
        window._journal.update_tracked(
            position_id,
            "Listed for sale",
            first.buy_price,
            None,
        )
        window._journal.add("Rune platebody", 12, 37_500, 39_250)
        window._render_journal()
        window.nav.setCurrentRow(2)
        save_widget(window, output_dir / "trade-journal.png", app)

        window.nav.setCurrentRow(3)
        save_widget(window, output_dir / "alch-finder.png", app)

        settings = SettingsDialog("Dark", window)
        settings.resize(680, 680)
        tabs = settings.findChild(QTabWidget)
        if tabs is not None:
            tabs.setCurrentIndex(1)
        settings.show()
        save_widget(settings, output_dir / "updates.png", app)
        settings.close()
        window.close()
    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
