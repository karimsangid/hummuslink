# HummusLink - register a Windows scheduled task that runs the server at logon,
# auto-restarts on failure, and starts even if a scheduled run was missed.
# Run: powershell -ExecutionPolicy Bypass -File install-task.ps1

$ErrorActionPreference = "Stop"

$TaskName = "HummusLink"
$Here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$Script   = Join-Path $Here "main.py"

if (-not (Test-Path $Script)) {
    throw "main.py not found at $Script"
}

# Prefer the real pythoncore install (has uvicorn/fastapi/pystray etc).
# Avoid the WindowsApps Store shim - it either fails silently or points at a
# different interpreter without HummusLink's deps.
$PreferredPaths = @(
    "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\pythonw.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python314\pythonw.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\pythonw.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
)
$pythonw = $null
foreach ($p in $PreferredPaths) {
    if (Test-Path $p) { $pythonw = $p; break }
}
if (-not $pythonw) {
    $cand = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if ($cand -and ($cand -notmatch 'WindowsApps')) { $pythonw = $cand }
}
if (-not $pythonw) {
    throw "No real pythonw.exe found (only the WindowsApps shim). Install Python from python.org or the Store."
}
Write-Host ("Using pythonw: {0}" -f $pythonw)

$RunBat = Join-Path $Here "run-service.bat"
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$RunBat`"" -WorkingDirectory $Here
$Trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$Settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries `
                -StartWhenAvailable `
                -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit (New-TimeSpan -Seconds 0)
# S4U (not Interactive) — matches the live hand-fixed task: cmd.exe hosting
# run-service.bat runs in a non-interactive session, so no console window.
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType S4U -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName `
    -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal `
    -Description "HummusLink cross-platform sync bridge on port 8765." -Force | Out-Null

Write-Host "Registered scheduled task: $TaskName" -ForegroundColor Green

# Remove the old Startup folder VBS if present so it does not double-start alongside the task.
$VbsPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\hummuslink.vbs"
if (Test-Path $VbsPath) {
    Remove-Item $VbsPath -Force
    Write-Host "Removed legacy Startup entry: $VbsPath" -ForegroundColor Yellow
}

# Free port 8765 so the scheduled task can bind cleanly.
# Kill whoever is listening AND any stray python main.py processes.
$listener = Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host ("Stopping existing listener on 8765, PID {0}" -f $listener[0].OwningProcess)
    Stop-Process -Id $listener[0].OwningProcess -Force -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match 'main\.py' -and
        $_.CommandLine -notmatch 'hummus-pulse' -and
        $_.CommandLine -notmatch 'daemon\.py'
    } |
    ForEach-Object {
        Write-Host ("Stopping stray HummusLink python PID {0}" -f $_.ProcessId)
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 3

Write-Host "Starting task now..."
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4

$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Host ""
Write-Host ("  LastRunTime:    {0}" -f $info.LastRunTime)
Write-Host ("  LastTaskResult: 0x{0:X8}" -f $info.LastTaskResult)

$listening = Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue
if ($listening) {
    Write-Host ("Port 8765 is LISTENING (PID {0})" -f $listening[0].OwningProcess) -ForegroundColor Green
} else {
    Write-Host "Port 8765 is NOT listening - check logs or run main.py manually" -ForegroundColor Red
}
