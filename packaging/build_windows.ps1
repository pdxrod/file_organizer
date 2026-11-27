# Build the Windows release: dist\FileOrganizerSetup.exe
# Run on windows-latest (see .github/workflows/release.yml).
# Every risky step's output is captured to build\*.log so the workflow's
# on-failure diagnostics step can publish them to the ci-diagnostics branch.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m venv build\venv
& build\venv\Scripts\python -m pip install --quiet --upgrade pip
& build\venv\Scripts\python -m pip install --quiet pyinstaller pyyaml flask
if ($LASTEXITCODE -ne 0) { throw "pip install failed ($LASTEXITCODE)" }

& build\venv\Scripts\python -m PyInstaller `
    --noconfirm --clean --onedir --name organize --console `
    --paths src `
    --collect-submodules file_organizer `
    --collect-submodules flask `
    packaging\computer_entry.py *>&1 | Tee-Object build\pyinstaller.log
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
if (-not (Test-Path dist\organize\organize.exe)) {
    throw "PyInstaller produced no dist\organize\organize.exe"
}

# Smoke-check the frozen binary.
& dist\organize\organize.exe version *>&1 | Tee-Object build\smoke.log
if ($LASTEXITCODE -ne 0) { throw "frozen binary smoke check failed ($LASTEXITCODE)" }

# Stage the files the installer copies next to the binary.
Copy-Item config_template.yaml dist\organize\
Copy-Item src\phone_pull.sh dist\organize\

# Stamp the version into the Inno Setup script, then compile the installer.
$version = (Select-String -Path src\file_organizer\__init__.py `
    -Pattern '__version__ = "([^"]+)"').Matches.Groups[1].Value
if (-not $version) { throw "could not read the version from __init__.py" }
$iss = Get-Content packaging\file_organizer.iss -Raw
$iss = $iss.Replace("{{VERSION}}", $version)
Set-Content -Path build\file_organizer.iss -Value $iss -Encoding UTF8

choco install innosetup --no-progress -y *>&1 | Tee-Object build\choco.log
if ($LASTEXITCODE -ne 0) { throw "choco install innosetup failed ($LASTEXITCODE)" }
& "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" /Qp build\file_organizer.iss *>&1 | Tee-Object build\iscc.log
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed ($LASTEXITCODE)" }
if (-not (Test-Path dist\FileOrganizerSetup.exe)) {
    throw "dist\FileOrganizerSetup.exe was not produced"
}
Write-Host "Built dist\FileOrganizerSetup.exe"
