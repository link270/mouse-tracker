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

This launches the always-on-top overlay and a minimal control panel where you can toggle individual effects or edit `config.json` live. Pass `--nogui` if you want to run only the overlay without the control panel.

Keyboard shortcuts (customize them in `config.json`):

- `Ctrl+Shift+Q` — quit the overlay
- `Ctrl+Shift+A` — toggle freehand drawing
- `Ctrl+Shift+S` — toggle cursor ring + comet tail
- `Ctrl+Shift+D` — toggle all click effects
- `Ctrl+Shift+F` — hold to spotlight the cursor

You can also click the control panel's quit button to exit. Adjust any other values in the same file to tweak colors, sizes, timings, or behavior; changes apply immediately when saved from the panel or on the next launch.
