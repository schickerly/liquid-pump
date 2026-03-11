"""Windows launcher/updater for the Liquid Pump app.

Intended flow for operators:
1) Run this EXE on the pump laptop.
2) It pulls the latest code from GitHub.
3) It launches pump_controller.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

DEFAULT_REPO_URL = "https://github.com/YOUR_ORG/YOUR_REPO.git"
REPO_URL = DEFAULT_REPO_URL
REPO_DIR = Path.home() / "liquid-pump"
BRANCH = "main"


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True)


def validate_config() -> None:
    if "YOUR_ORG" in REPO_URL or "YOUR_REPO" in REPO_URL or REPO_URL == DEFAULT_REPO_URL:
        raise ValueError(
            "REPO_URL is still set to the placeholder value. "
            "Edit updater_launcher.py and set REPO_URL to your real GitHub repo URL, "
            "for example: https://github.com/my-org/liquid-pump.git"
        )


def ensure_repo() -> None:
    if (REPO_DIR / ".git").exists():
        run(["git", "fetch", "origin"], cwd=REPO_DIR)
        run(["git", "checkout", BRANCH], cwd=REPO_DIR)
        run(["git", "reset", "--hard", f"origin/{BRANCH}"], cwd=REPO_DIR)
    else:
        REPO_DIR.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--branch", BRANCH, REPO_URL, str(REPO_DIR)])


def launch_app() -> None:
    app_file = REPO_DIR / "pump_controller.py"
    if not app_file.exists():
        raise FileNotFoundError(f"Could not find {app_file}")

    run([sys.executable, str(app_file)], cwd=REPO_DIR)


def main() -> int:
    try:
        validate_config()
        ensure_repo()
        launch_app()
        return 0
    except Exception as exc:
        print(f"Launcher failed: {exc}")
        input("Press Enter to close...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
