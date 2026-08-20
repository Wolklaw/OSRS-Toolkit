$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ReleaseDir = Join-Path $ProjectRoot "release"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Development environment not found. Create .venv and install the dev dependencies first."
}

$ProjectVersion = (& $Python -c "from osrs_toolkit import __version__; print(__version__)").Trim()
if (-not $ProjectVersion) {
    throw "Could not determine the application version."
}
$PortableZip = Join-Path $ReleaseDir "OSRS-Toolkit-Portable-$ProjectVersion.zip"

# One process for the whole suite. This used to be split across six, because a run would
# die partway through with no failing test to point at; the cause was the start-up update
# check firing a real network request from a QThread that outlived the window that started
# it, which tests/conftest.py now disables. Splitting the run hid that rather than fixing
# it, and cost the suite its ability to catch anything one test leaks into the next.
& $Python -m pytest -q (Join-Path $ProjectRoot "tests")
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
& $Python -m ruff check src tests tools
if ($LASTEXITCODE -ne 0) { throw "Code-quality checks failed." }
& $Python tools\make_icon.py
if ($LASTEXITCODE -ne 0) { throw "Icon generation failed." }
& $Python tools\make_installer_art.py
if ($LASTEXITCODE -ne 0) { throw "Installer artwork generation failed." }
# Nuitka, not PyInstaller, since 1.1 — packaging\build_app.py says why, and carries the
# full argument list. Expect this step to take minutes rather than seconds: it is a C
# compile of the whole application and its dependencies, not an archive of them.
& $Python packaging\build_app.py
if ($LASTEXITCODE -ne 0) { throw "Application packaging failed." }

New-Item -ItemType Directory -Path $ReleaseDir -Force | Out-Null
if (Test-Path -LiteralPath $PortableZip) {
    Remove-Item -LiteralPath $PortableZip
}
Compress-Archive -LiteralPath (Join-Path $ProjectRoot "dist\OSRS Toolkit") -DestinationPath $PortableZip -CompressionLevel Optimal

$InnoCompiler = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if ($InnoCompiler) {
    & $InnoCompiler "/DMyAppVersion=$ProjectVersion" (Join-Path $ProjectRoot "packaging\installer.iss")
    if ($LASTEXITCODE -ne 0) { throw "Installer compilation failed." }
    Write-Host "Portable ZIP and setup wizard created in $ReleaseDir"
} else {
    Write-Host "Portable ZIP created in $ReleaseDir"
    Write-Host "Install Inno Setup 6 to also compile packaging\installer.iss into a setup wizard."
}
