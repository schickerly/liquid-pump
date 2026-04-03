"""Windows launcher/updater for the Liquid Pump app."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_URL = "https://github.com/schickerly/liquid-pump.git"
REPO_DIR = Path.home() / "liquid-pump"
# Primary deploy branch that contains pump_controller.py
BRANCH = "codex/create-gui-for-pump-control-system-zyq18x"
LAUNCHER_VERSION = "2026.03.11.4"
# Keep fallback empty to avoid accidentally switching to branches without pump_controller.py.
FALLBACK_BRANCHES: list[str] = []


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def print_diagnostics() -> None:
    print(f"PumpLauncher version: {LAUNCHER_VERSION}")
    print(f"Executable: {Path(sys.executable).resolve()}")
    print(f"Repo dir: {REPO_DIR}")
    print(f"Using repo: {REPO_URL} | branch: {BRANCH}")


def validate_config() -> None:
    if "/tree/" in REPO_URL:
        raise ValueError(
            "REPO_URL must be a clone URL ending in .git, not a GitHub /tree/<branch> page."
        )


def sync_branch(branch: str) -> None:
    run(["git", "fetch", "origin", branch], cwd=REPO_DIR)
    run(["git", "checkout", branch], cwd=REPO_DIR)
    # Safer on shared machines: update only via fast-forward pulls.
    run(["git", "pull", "--ff-only", "origin", branch], cwd=REPO_DIR)


def ensure_repo() -> None:
    if (REPO_DIR / ".git").exists():
        sync_branch(BRANCH)
    else:
        REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--branch", BRANCH, REPO_URL, str(REPO_DIR)])


def ensure_app_file() -> Path:
    app_file = REPO_DIR / "pump_controller.py"
    if app_file.exists():
        return app_file

    print("pump_controller.py missing on primary branch; trying fallback branches...")
    for branch in FALLBACK_BRANCHES:
        try:
            sync_branch(branch)
        except Exception as exc:
            print(f"Fallback branch sync failed for {branch}: {exc}")
            continue
        if app_file.exists():
            print(f"Recovered app file on fallback branch: {branch}")
            return app_file

    raise FileNotFoundError(
        f"Could not find {app_file}. Verify BRANCH is correct and contains pump_controller.py"
    )


def launch_app(app_file: Path) -> None:
    run([sys.executable, str(app_file)], cwd=REPO_DIR)


def main() -> int:
    try:
        validate_config()
        print_diagnostics()
        if "--diagnose" in sys.argv:
            print("Diagnostic mode only; exiting without syncing or launching app.")
            return 0

        ensure_repo()
        app_file = ensure_app_file()
        launch_app(app_file)
        return 0
    except Exception as exc:
        print(f"Launcher failed: {exc}")
        print("Tip: Ensure GitHub credentials are configured if the repo is private.")
        input("Press Enter to close...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
