# Mouse Tracker Overlay

This project opens a translucent overlay that highlights your mouse cursor, draws color-coded markers for clicks, and paints drag trails that fade out after a few seconds. It is handy for demos, tutorials, or tracking your own activity.

## Prerequisites

- Python 3.9 or newer
- `pip` (typically bundled with Python)

## Setup

```powershell
# Clone or download this project, then from the repo directory:
python -m venv .venv
.\\.venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```

## Running the overlay

```powershell
python mouse_overlay.py
```

Press `Ctrl+Shift+Q` to exit the overlay (customize `exit_hotkey` in `config.json`). Adjust any other values in the same file to tweak colors, sizes, timings, or behavior; changes apply the next time you launch the script.
