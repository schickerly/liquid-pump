# Creates "Liquid Pump Fill.lnk" on the desktop — runs git pull then the GUI.
# Run once from PowerShell (repo can live anywhere):
#   Set-ExecutionPolicy -Scope Process Bypass -Force
#   cd C:\path\to\liquid-pump\scripts
#   .\create_desktop_shortcut_fill.ps1

param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
)

$runner = Join-Path $RepoRoot "scripts\run_pump_fill_gui.cmd"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Not found: $runner"
}

$wsh = New-Object -ComObject WScript.Shell
$lnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Liquid Pump Fill.lnk"
$sc = $wsh.CreateShortcut($lnkPath)
$sc.TargetPath = $runner
$sc.WorkingDirectory = $RepoRoot
$sc.Description = "Liquid Pump: git pull and open fill GUI"
$sc.Save()

Write-Host "Created: $lnkPath"
