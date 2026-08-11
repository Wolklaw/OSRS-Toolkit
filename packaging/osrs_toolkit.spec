# PyInstaller definition shared by the portable package and installed application.

from pathlib import Path

project_root = Path(SPECPATH).parent

a = Analysis(
    [str(project_root / "src" / "osrs_toolkit" / "__main__.py")],
    pathex=[str(project_root / "src")],
    binaries=[],
    datas=[(str(project_root / "assets" / "osrs_toolkit.ico"), "assets")],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="OSRS Toolkit",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(project_root / "assets" / "osrs_toolkit.ico"),
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="OSRS Toolkit",
)
