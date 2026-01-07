Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Runs AuthReferee from this folder in PowerShell.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $here

function Resolve-PythonExecutable {
    # 0) Prefer local venv (ensures dependencies like textual/psutil are present)
    $venvPy = Join-Path $here '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $venvPy) {
        return $venvPy
    }

    # Prefer Python 3.11 per spec, then 3.12, then any available.
    $candidates = New-Object System.Collections.Generic.List[string]

    # 1) Windows Python launcher (py)
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($ver in @('3.11', '3.12', '')) {
            try {
                $args = @()
                if ($ver -ne '') { $args += "-$ver" }
                $args += @('-c', 'import sys; print(sys.executable)')
                $exe = & $py.Source @args 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path -LiteralPath $exe)) {
                    return $exe.Trim()
                }
            }
            catch { }
        }
    }

    # 2) python/python3 on PATH
    foreach ($name in @('python3', 'python')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) {
            try {
                $exe = & $cmd.Source -c 'import sys; print(sys.executable)' 2>$null
                if ($LASTEXITCODE -eq 0 -and $exe -and (Test-Path -LiteralPath $exe)) {
                    return $exe.Trim()
                }
            }
            catch { }
        }
    }

    # 3) Common per-user install locations
    $roots = @(
        "$env:LocalAppData\Programs\Python",
        "$env:ProgramFiles\Python",
        "$env:ProgramFiles(x86)\Python"
    ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }

    $found = @()
    foreach ($root in $roots) {
        $found += Get-ChildItem -LiteralPath $root -Filter python.exe -Recurse -ErrorAction SilentlyContinue
    }

    $best = $found |
        Sort-Object -Property FullName -Descending |
        Select-Object -First 1

    if ($best) {
        return $best.FullName
    }

    return $null
}

$pythonExe = Resolve-PythonExecutable
if (-not $pythonExe) {
    Write-Host "Could not find Python. Install Python 3.11+ and try again." -ForegroundColor Red
    Write-Host "Tip: run 'py --list' or ensure python.exe is on PATH." -ForegroundColor DarkGray
    exit 1
}

& $pythonExe "$here\main.py"

Write-Host "" 
Write-Host "Press Enter to close..." -ForegroundColor DarkGray
[void][System.Console]::ReadLine()
