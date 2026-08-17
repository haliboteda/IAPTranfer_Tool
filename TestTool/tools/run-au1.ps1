# AU1 -- drives both phases of the nonce-uniqueness case around a real power cut.
#
#   .\run-au1.ps1                    full run, prompts you to pull the plug
#   .\run-au1.ps1 -Count 12          take 12 nonces per phase instead of 8
#   .\run-au1.ps1 -Ip 192.168.0.7    override the address from config
#   .\run-au1.ps1 -Resume            keep the nonces already collected and go
#                                    straight to waiting for the power cut
#
# Why a script and not one TestTool invocation: the network goes away with the
# power, so the nonces from before the cut have to survive on disk. TestTool
# does the protocol and the verdict; this file does the choreography.
#
# It never asks you to press Enter. It watches the board's own UDP discovery to
# see the power go and come back, so the timing recorded is the board's, not a
# human's reaction time -- and so the script cannot be fooled by somebody
# confirming a power cut that did not happen.
#
# Exit 0 = AU1 passed, 1 = it failed, 2 = the run could not be set up.

param(
    [string]$Ip,
    [int]$Count = 8,
    [string]$Port = "56865",
    # Generous by default: the thing being waited on is a person walking to a
    # board, and a timeout here throws away a phase 1 that was perfectly good.
    [int]$WaitMinutes = 20,
    [switch]$Resume,
    [string[]]$Ports
)

. "$PSScriptRoot\_common.ps1"

if (-not $Ports) { $Ports = $LOG_PORTS }
if (-not $Ip)    { $Ip = $BOARD_IP }
if (-not $Ip)    { Fail "need -Ip (or set BOARD_IP in config/machine.ps1)"; exit 2 }

$testTool = Join-Path $TOOL_REPO "Output\windows\TestTool.exe"
if (-not (Test-Path $testTool)) {
    Warn "TestTool.exe not built, building it now"
    Push-Location $TOOL_REPO
    go build -o "Output/windows/TestTool.exe" ./TestTool
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0 -or -not (Test-Path $testTool)) { Fail "cannot build TestTool"; exit 2 }
}

$stateFile = Join-Path $env:TEMP "au1_phase1.json"
if (-not $Resume -and (Test-Path $stateFile)) { Remove-Item $stateFile -Force }

# One UDP discovery query. Returns the identity string, or $null when the board
# does not answer -- which is how "the power is off" is detected.
function Probe-Board([string]$addr, [string]$udpPort, [int]$timeoutMs = 1500) {
    $client = New-Object System.Net.Sockets.UdpClient
    try {
        $client.Client.ReceiveTimeout = $timeoutMs
        $client.Connect($addr, [int]$udpPort)
        $msg = [Text.Encoding]::ASCII.GetBytes("openplc_server_where_r_y")
        [void]$client.Send($msg, $msg.Length)
        $remote = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
        $data = $client.Receive([ref]$remote)
        return [Text.Encoding]::ASCII.GetString($data).Trim()
    } catch {
        return $null
    } finally {
        $client.Close()
    }
}

# Wait for the board to go quiet ($Present = $false) or come back ($true).
# Requires three consecutive agreeing probes: a single missed datagram is normal
# on UDP and would otherwise read as a power cut.
function Wait-BoardState([bool]$Present, [string]$What, [int]$Minutes) {
    $deadline = (Get-Date).AddMinutes($Minutes)
    $streak = 0
    while ((Get-Date) -lt $deadline) {
        $reply = Probe-Board $Ip $Port
        $seen = ($null -ne $reply)
        if ($seen -eq $Present) {
            $streak++
            if ($streak -ge 3) {
                if ($Present) { Ok "  board is back ($reply)" } else { Ok "  board has gone quiet" }
                return $true
            }
        } else {
            $streak = 0
        }
        Start-Sleep -Milliseconds 700
    }
    Fail "  timed out after $Minutes min waiting for: $What"
    return $false
}

# ---------------------------------------------------------------- phase 1 ----

Section "AU1 phase 1 -- collecting nonces before the power cut"

if ($Resume -and (Test-Path $stateFile)) {
    $before = (Get-Content $stateFile -Raw | ConvertFrom-Json)
    $age = (New-TimeSpan -Start ([datetime]$before.taken) -End (Get-Date))
    Write-Host "resuming with $($before.samples.Count) nonces taken $([int]$age.TotalMinutes) min ago"
    # Stale state would still produce a verdict, just a meaningless one: the
    # power cut it is compared against might not be the one that happened.
    if ($age.TotalHours -gt 1) {
        Fail "that state is $([int]$age.TotalHours)h old -- too old to trust. Re-run without -Resume"
        exit 2
    }
} else {
    if ($Resume) { Warn "-Resume given but $stateFile does not exist; collecting phase 1 now" }

    & "$PSScriptRoot\enter-bootloader.ps1" -Ip $Ip -Ports $Ports
    if ($LASTEXITCODE -ne 0) { Fail "could not park the board in the bootloader"; exit 2 }

    & $testTool AU1 --ip=$Ip --port=$Port --state=$stateFile --phase=1 --count=$Count
    if ($LASTEXITCODE -ne 0) { Fail "phase 1 failed -- not sending you to the board for nothing"; exit 1 }

    $before = (Get-Content $stateFile -Raw | ConvertFrom-Json)
}
$lastCounter = ($before.samples | Select-Object -Last 1).counter

# ------------------------------------------------------------ power cycle ----

Section "PULL THE POWER NOW"
Write-Host ""
Write-Host "  Remove power from the board -- unplug it, do not press reset." -ForegroundColor Yellow
Write-Host "  A reset never touches the RTC backup domain, so a reset here would" -ForegroundColor Yellow
Write-Host "  pass even on a board with a dead VBAT cell. That board is exactly" -ForegroundColor Yellow
Write-Host "  what this case exists to catch." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Waiting for the board to go quiet..."

if (-not (Wait-BoardState $false "the board to stop answering" $WaitMinutes)) { exit 2 }

Section "NOW RESTORE POWER"
Write-Host ""
Write-Host "  Plug it back in. Capturing the boot log while it comes up." -ForegroundColor Yellow
Write-Host ""

# Hold the log ports open across power-up so the bootloader's own counter report
# is captured. That line is independent corroboration: it is read straight out
# of the backup register, not out of a nonce.
$open = Open-LogPorts $Ports
if (-not (Wait-BoardState $true "the board to answer again" $WaitMinutes)) {
    foreach ($k in @($open.Keys)) { try { $open[$k].Close() } catch {} }
    exit 2
}
$bootLog = ""
$drain = Read-LogPorts $open 3
foreach ($k in $drain.Keys) { $bootLog += $drain[$k] }

if ($bootLog.Trim()) {
    Section "boot log"
    Write-Host $bootLog
}

# ---------------------------------------------------------------- phase 2 ----

Section "AU1 phase 2 -- collecting nonces after the power cut"

# Captured, not just printed: the bootloader's own counter report lands in this
# output rather than in the power-up log above (see the cross-check below).
#
# 6>&1, not 2>&1. Every line enter-bootloader prints goes through Write-Host,
# which on PowerShell 5.1 writes to the information stream (6), not the success
# stream. Capturing with 2>&1 yields an empty string while the text still
# appears on the console -- so the cross-check silently had nothing to search
# and said so, which reads like the board never printed the line.
$ebOut = (& "$PSScriptRoot\enter-bootloader.ps1" -Ip $Ip -Ports $Ports 6>&1 2>&1 | Out-String -Width 4096)
$ebCode = $LASTEXITCODE
Write-Host $ebOut
if ($ebCode -ne 0) { Fail "board came back but could not be parked in the bootloader"; exit 2 }

& $testTool AU1 --ip=$Ip --port=$Port --state=$stateFile --phase=2 --count=$Count
$verdict = $LASTEXITCODE

# ------------------------------------------------------- serial cross-check ---

Section "cross-check against the bootloader's own report"

# The bootloader prints the counter it read from the backup register
# (IAPServer/iap_auth.c iap_auth_report_backup_domain). Comparing that to the
# last nonce issued before the cut tests the same claim through a completely
# different path: the register read directly, rather than a counter inferred
# from the first four bytes of a nonce. If those two disagree, one of them is
# not reading what it claims to.
#
# Both logs are searched because that line is NOT on the power-up boot log: the
# report only runs when the bootloader stays in upload mode, and a board with a
# valid application hands off before reaching it. It shows up in the
# enter-bootloader output instead. Looking only at the boot log reported
# "cross-check not available" on a run where the number was right there.
$m = [regex]::Match($bootLog + "`n" + $ebOut, "nonce counter = (\d+)")
if (-not $m.Success) {
    Warn "no 'nonce counter =' line in either log -- cross-check not available"
    Warn "(the verdict above still stands; it just has no second opinion)"
} else {
    $reported = [uint32]$m.Groups[1].Value
    Write-Host "  last nonce issued before the cut : $lastCounter"
    Write-Host "  counter reported after power-up  : $reported"
    if ($reported -lt $lastCounter) {
        Fail "  the backup register came back BELOW where the nonces had reached -- it did not survive"
        $verdict = 1
    } else {
        Ok "  the backup register survived the power cut and did not go backwards"
    }
}

if ($bootLog -match "Backup domain was lost") {
    Fail "  the board itself reports the backup domain was lost -- replay protection is weakened"
    $verdict = 1
} elseif ($bootLog -match "Backup domain retained") {
    Ok "  board reports: Backup domain retained"
}

Section "result"
if ($verdict -ne 0) { Fail "AU1 FAILED"; exit 1 }
Ok "AU1 passed -- nonces are unique and the counter survived a real power cut"
Write-Host "state kept at: $stateFile"
exit 0
