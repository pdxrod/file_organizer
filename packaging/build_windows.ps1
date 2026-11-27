# Build the Windows release: dist\FileOrganizerSetup.exe
# Run on windows-latest (see .github/workflows/release.yml).
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m venv build\venv
& build\venv\Scripts\python -m pip install --quiet --upgrade pip
& build\venv\Scripts\python -m pip install --quiet pyinstaller pyyaml flask

& build\venv\Scripts\python -m PyInstaller `
    --noconfirm --clean --onedir --name organize --console `
    --paths src `
    --collect-submodules file_organizer `
    --collect-submodules flask `
    packaging\computer_entry.py

# Smoke-check the frozen binary.
& dist\organize\organize.exe version

# Stage the files the installer copies next to the binary.
Copy-Item config_template.yaml dist\organize\
Copy-Item src\phone_pull.sh dist\organize\

# Stamp the version into the Inno Setup script, then compile the installer.
$version = (Select-String -Path src\file_organizer\__init__.py `
    -Pattern '__version__ = "([^"]+)"').Matches.Groups[1].Value
$iss = Get-Content packaging\file_organizer.iss -Raw
$iss = $iss.Replace("{{VERSION}}", $version)
Set-Content -Path build\file_organizer.iss -Value $iss -Encoding UTF8

choco install innosetup --no-progress -y
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp build\file_organizer.iss
Write-Host "Built dist\FileOrganizerSetup.exe"
