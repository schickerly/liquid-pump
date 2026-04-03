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

## Shared PC safe update mode

For a shared machine, use read-only GitHub credentials and fast-forward-only pulls:

- Setup guide: `SHARED_PC_GIT_PULL_SETUP.md`
- Credential helper script: `scripts/setup_readonly_git_cred.ps1`

## Seaflow pump + Arduino smoke test

- Firmware: `arduino/pump_motor_tester/pump_motor_tester.ino` (upload in Arduino IDE)
- Wiring notes: `arduino/pump_motor_tester/WIRING.md`
- PC GUI (from repo folder):

```powershell
cd "C:\Users\schic\liquid-pump"
py -m pip install pyserial keyboard hidapi
py pump_motor_test_gui.py
```

- **Auto-fill** (target weight, ramp, drip reverse, purge): `py pump_fill_gui.py` (same deps: `pyserial`, `keyboard`, `hidapi`)

- **Fill GUI as EXE (field laptop):**
  1. On a dev machine with Python: run `build_pump_fill_gui.cmd` → `dist\PumpFillGui.exe`
  2. Commit/push the repo; on the field PC clone the repo once and set up read-only Git per `SHARED_PC_GIT_PULL_SETUP.md`
  3. Run `scripts\create_desktop_shortcut_fill.ps1` once to put **Liquid Pump Fill** on the desktop. That shortcut runs `scripts\run_pump_fill_gui.cmd`, which does `git pull --ff-only` then starts `pump_fill_gui.py` (needs Python on PATH). Optional: build `dist\PumpFillGui.exe` for offline use and run it directly without going through the launcher.

- **Motor test EXE:** run `build_pump_motor_test_gui.cmd` → `dist\PumpMotorTestGui.exe`
- **Motor test launcher:** `scripts\run_pump_motor_gui.cmd` (pull + run exe or `.py`)

- Shared PC: use read-only Git credentials per `SHARED_PC_GIT_PULL_SETUP.md` (no personal browser login required).

## Build a Windows EXE that updates from GitHub, then starts the pump app

`updater_launcher.py` now targets the branch that contains the GUI app:
- `REPO_URL = https://github.com/schickerly/liquid-pump.git`
- `BRANCH = codex/create-gui-for-pump-control-system-zyq18x`

If an old local clone points to a branch that does not contain `pump_controller.py`,
the launcher now attempts fallback recovery and prints clear diagnostics.

## Command Prompt commands (copy/paste)

### A) Fresh clone of the correct branch

```cmd
cd %USERPROFILE%
git clone --branch codex/create-gui-for-pump-control-system-zyq18x https://github.com/schickerly/liquid-pump.git
cd liquid-pump
```

### B) Update an existing local clone to the correct branch

```cmd
cd %USERPROFILE%\liquid-pump
git fetch origin
git checkout codex/create-gui-for-pump-control-system-zyq18x
git reset --hard origin/codex/create-gui-for-pump-control-system-zyq18x
```

### C) Build the EXE again

```cmd
py -m pip install --upgrade pip
py -m pip install pyinstaller
py -m PyInstaller --onefile --name PumpLauncher updater_launcher.py
```

EXE output:

- `dist\PumpLauncher.exe`


### Verified build script (recommended)

To avoid ambiguity, run this from `C:\Users\schic\liquid-pump`:

```cmd
build_verified_launcher.cmd
```

It performs extra checks before build:
1. Confirms `updater_launcher.py` is pinned to `codex/create-gui-for-pump-control-system-zyq18x`
2. Confirms expected launcher version string
3. Builds with `--clean`
4. Runs `dist\PumpLauncher.exe --diagnose` so you can confirm the built binary itself reports the expected branch

If diagnose output still says `main`, then the EXE being launched is not the one at `dist\PumpLauncher.exe` in your current folder.

### D) Clean rebuild if an old EXE still pulls wrong branch

```cmd
cd %USERPROFILE%
if exist liquid-pump rmdir /s /q liquid-pump
git clone --branch codex/create-gui-for-pump-control-system-zyq18x https://github.com/schickerly/liquid-pump.git
cd liquid-pump
py -m pip install pyinstaller
py -m PyInstaller --onefile --name PumpLauncher updater_launcher.py
```

Then replace old launcher with `dist\PumpLauncher.exe`.

## Troubleshooting

### Error: `Could not find ...\pump_controller.py`

This means the local repo is on a branch without the GUI file.

Fix by forcing the correct branch:

```cmd
cd %USERPROFILE%\liquid-pump
git fetch origin
git checkout codex/create-gui-for-pump-control-system-zyq18x
git reset --hard origin/codex/create-gui-for-pump-control-system-zyq18x
```

Then rebuild and run the EXE again.


### Error still shows `git checkout main`

If launcher output shows `git checkout main`, you are running an **old EXE** built before the branch fix.

Use this exact reset + rebuild flow:

```cmd
cd %USERPROFILE%
if exist liquid-pump rmdir /s /q liquid-pump
if exist PumpLauncher.exe del /f /q PumpLauncher.exe
git clone --branch codex/create-gui-for-pump-control-system-zyq18x https://github.com/schickerly/liquid-pump.git
cd liquid-pump
py -m pip install --upgrade pip
py -m pip install pyinstaller
py -m PyInstaller --onefile --name PumpLauncher updater_launcher.py
copy /y dist\PumpLauncher.exe %USERPROFILE%\Desktop\PumpLauncher.exe
```

When you run the new EXE, you should see:
- `PumpLauncher version: 2026.03.11.4`
- `Using repo: https://github.com/schickerly/liquid-pump.git | branch: codex/create-gui-for-pump-control-system-zyq18x`



### Why `pump_controller.py` appears then disappears

That behavior means a **different/older EXE** is being launched, and it is checking out `main` then hard-resetting.
This rewrites `C:\Users\schic\liquid-pump` and removes files not on `main` (including `pump_controller.py`).

Use this verification rule:
- New EXE must print `PumpLauncher version: 2026.03.11.4`
- New EXE must print `Executable: <full path>` so you can confirm which file is actually running
- New EXE must print `branch: codex/create-gui-for-pump-control-system-zyq18x`

To avoid stale copies, always run the EXE directly from `dist\PumpLauncher.exe` right after build, then copy that exact file to Desktop.

