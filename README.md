# AUTHREFEREE

Terminal-based authentication decision engine.

## Requirements

- Python 3.11+
- `textual`
- `psutil`

## Install

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

## Run

```powershell
.\.venv\Scripts\python main.py
```

### Quick launch helpers (Windows)

- Double-click (Explorer): `run.bat`
- PowerShell: `./run.ps1`
- Git Bash / WSL (opens PowerShell): `./run.sh` (optional)

## What it does

- Asks 6 questions in a fixed order
- Scores 4 authentication strategies deterministically (rule-based)
- Ranks all four and shows a live-updating Textual dashboard (cyberpunk console)
