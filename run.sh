#!/usr/bin/env bash
# Launches AuthReferee inside Windows PowerShell.
# Usage: ./run.sh (from Git Bash or WSL)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WIN_DIR="$SCRIPT_DIR"
if command -v cygpath >/dev/null 2>&1; then
	WIN_DIR="$(cygpath -w "$SCRIPT_DIR")"
elif command -v wslpath >/dev/null 2>&1; then
	WIN_DIR="$(wslpath -w "$SCRIPT_DIR")"
fi

# Prefer Python 3.11 per spec; fall back to whatever `py` has.
POWERSHELL_CMD="Set-Location -LiteralPath '$WIN_DIR'; "
POWERSHELL_CMD+="try { py -3.11 main.py } catch { py main.py }"

# -NoExit keeps the PowerShell window open after the program exits.
PS_EXE=""
if command -v powershell.exe >/dev/null 2>&1; then
	PS_EXE="powershell.exe"
elif command -v pwsh.exe >/dev/null 2>&1; then
	PS_EXE="pwsh.exe"
elif [ -x "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" ]; then
	PS_EXE="/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
elif [ -x "/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe" ]; then
	PS_EXE="/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe"
fi

if [ -z "$PS_EXE" ]; then
	echo "Error: Could not find Windows PowerShell (powershell.exe)." >&2
	echo "Run with PowerShell instead: ./run.ps1 (or double-click run.cmd)." >&2
	exit 1
fi

"$PS_EXE" -NoExit -ExecutionPolicy Bypass -Command "$POWERSHELL_CMD"