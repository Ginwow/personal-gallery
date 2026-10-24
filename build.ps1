param([switch]$Release, [string]$PythonExecutable = "")
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
if ($PythonExecutable) { $python = $PythonExecutable }
& $python -m PyInstaller --noconfirm --windowed --onedir --icon "$PSScriptRoot/assets/gallery.ico" --add-data "$PSScriptRoot/assets/gallery.ico;assets" --name '个人画廊' --distpath dist --workpath build --specpath build --paths . main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Copy-Item -LiteralPath README.md -Destination 'dist/个人画廊/README.md' -Force
Copy-Item -LiteralPath THIRD_PARTY_NOTICES.md -Destination 'dist/个人画廊/THIRD_PARTY_NOTICES.md' -Force
Copy-Item -LiteralPath licenses -Destination 'dist/个人画廊' -Recurse -Force
if ($Release) {
    & $python scripts/package_release.py 'dist/个人画廊' 'dist/PersonalGallery-Windows-x64.zip'
    if ($LASTEXITCODE -ne 0) { throw 'Package failed' }
}
