$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    throw "Virtual environment not found. Run .\\setup.ps1 first."
}

& .\.venv\Scripts\python.exe -m pytest
& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean road_matcher.spec

Write-Host "Standalone application created at dist\\RoadMatcher\\RoadMatcher.exe"
Write-Host "To create Setup.exe, install Inno Setup and compile installer\\RoadMatcher.iss"
