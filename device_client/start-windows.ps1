$ErrorActionPreference = 'Stop'

function Test-Python310 {
    param([string]$Executable, [string[]]$PrefixArgs)
    try {
        & $Executable @PrefixArgs -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' *> $null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

try {
    $Python = $null
    $PythonArgs = @()
    $PyLauncher = Get-Command py -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($PyLauncher -and (Test-Python310 -Executable $PyLauncher.Source -PrefixArgs @('-3'))) {
        $Python = $PyLauncher.Source
        $PythonArgs = @('-3')
    } else {
        $PythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($PythonCommand -and (Test-Python310 -Executable $PythonCommand.Source -PrefixArgs @())) {
            $Python = $PythonCommand.Source
        }
    }
    if (-not $Python) {
        throw 'Python 3.10+ was not found. Install Python with Tcl/Tk support yourself, then retry.'
    }

    if ($env:LOCALAPPDATA) {
        $Venv = Join-Path $env:LOCALAPPDATA 'DeviceBridge\venv'
        try {
            New-Item -ItemType Directory -Force -Path $Venv | Out-Null
        } catch {
            $Venv = Join-Path $PSScriptRoot '.venv'
            New-Item -ItemType Directory -Force -Path $Venv | Out-Null
        }
    } else {
        $Venv = Join-Path $PSScriptRoot '.venv'
        New-Item -ItemType Directory -Force -Path $Venv | Out-Null
    }
    $VenvPython = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $VenvPython)) {
        & $Python @PythonArgs -m venv $Venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
    }
    if (-not (Test-Python310 -Executable $VenvPython -PrefixArgs @())) {
        throw "The existing virtual environment needs Python 3.10+. Remove only '$Venv' and retry."
    }
    & $VenvPython -c 'import tkinter, venv'
    if ($LASTEXITCODE -ne 0) { throw 'Python is missing Tcl/Tk or venv. Repair your Python installation.' }
    & $VenvPython -m pip install --disable-pip-version-check --requirement (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your network and pip configuration.' }
    & $VenvPython (Join-Path $PSScriptRoot 'desktop.py')
    if ($LASTEXITCODE -ne 0) { throw 'Device Bridge exited with an error.' }
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
