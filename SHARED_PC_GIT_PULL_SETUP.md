# Shared PC: Safe Git Pull Setup

This setup lets a shared Windows machine pull your latest private GitHub code with a read-only credential, without signing into your personal/work browser accounts.

## 1) Create a dedicated local Windows account

Use a separate standard (non-admin) account, for example `pump-runner`.

Run everything below while logged into that account.

## 2) Create a read-only GitHub token

Create a fine-grained personal access token with:

- Repository access: only `schickerly/liquid-pump`
- Permissions: read-only contents/metadata
- Expiration: short (for example 30-90 days)

Do not reuse any personal full-scope token.

## 3) Store credential in Windows Credential Manager

From PowerShell in this repo:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\scripts\setup_readonly_git_cred.ps1
```

When prompted, paste the read-only token.

## 4) Clone the repo under the dedicated account

```powershell
cd $env:USERPROFILE
git clone --branch codex/create-gui-for-pump-control-system-zyq18x https://github.com/schickerly/liquid-pump.git
```

## 5) Update behavior used by launcher

`updater_launcher.py` now uses:

- `git fetch origin <branch>`
- `git checkout <branch>`
- `git pull --ff-only origin <branch>`

This avoids destructive hard resets during routine updates.

## 6) Optional: run at logon with Task Scheduler

Use the dedicated account and point action to your launcher binary, for example:

- Program/script: `C:\Users\pump-runner\liquid-pump\dist\PumpLauncher.exe`
- Start in: `C:\Users\pump-runner\liquid-pump`

## 7) Lock down shared-machine risk

- Do not log into Gmail/work accounts in that Windows user
- Disable browser sync and password saving
- Keep this account non-admin
- Rotate token if anyone with admin rights may have accessed the machine
