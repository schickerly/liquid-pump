# Liquid Pump Starter

Starter desktop GUI for a pump fill workflow using:
- HID scale reads
- Serial relay control
- Keyboard trigger (foot pedal)
- Auto-stop at target weight
- CSV fill logging
- Basic USB-device visibility check hook

## Run locally

```bash
python3 pump_controller.py
```

## Build a Windows EXE that updates from GitHub, then starts the pump app

This project now includes `updater_launcher.py` for your pump laptop workflow:

1. Pull latest code from GitHub (`git fetch` + hard reset to your branch).
2. Start `pump_controller.py` from that updated repo.

### 1) Configure launcher values

Edit `updater_launcher.py` and set:
- `REPO_URL` to your GitHub repo URL
- `BRANCH` to the branch you deploy (usually `main`)

### Common error: "Repository not found"

If you see an error like this in `PumpLauncher.exe`:

- `https://github.com/YOUR_ORG/YOUR_REPO.git not found`

that means `REPO_URL` is still a placeholder.

Fix:
1. Open `updater_launcher.py`
2. Set `REPO_URL` to your real repo, e.g. `https://github.com/my-org/liquid-pump.git`
3. Rebuild the EXE with PyInstaller and replace the old EXE on the laptop

### 2) On a Windows build machine

Install prerequisites:

```powershell
py -m pip install pyinstaller
```

Build one-file EXE:

```powershell
py -m PyInstaller --onefile --name PumpLauncher updater_launcher.py
```

Your EXE will be in:

- `dist\PumpLauncher.exe`

### 3) Put EXE on the pump laptop

Install on laptop:
- `Git for Windows` (so `git` command exists in PATH)
- `Python` with required dependencies for the app (`hid`, `pyserial`, `keyboard`, `tkinter`)

Then run `PumpLauncher.exe`.

It will:
- clone repo to `%USERPROFILE%\liquid-pump` if missing
- otherwise force-sync it to the latest remote branch
- launch `pump_controller.py`

## Notes

- Update `AppConfig` in `pump_controller.py` for your relay COM port and scale VID/PID.
- The updater currently uses a hard reset to remote, which discards local edits on the laptop.
- This is a starter baseline for upcoming diagnostics (scale health checks, USB/device monitoring, recovery flows).
