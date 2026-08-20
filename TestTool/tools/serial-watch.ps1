# Watch the board's log ports. Touches nothing on the board unless -Reset.
#
#   .\serial-watch.ps1                 watch until Ctrl-C
#   .\serial-watch.ps1 -Seconds 30     watch for a while, then print
#   .\serial-watch.ps1 -Reset          reset over ST-Link first, to catch a boot log
#   .\serial-watch.ps1 -Ports COM7     override the ports from config

param(
    [int]$Seconds = 0,          # 0 = until Ctrl-C
    [switch]$Reset,
    [string[]]$Ports
)

. "$PSScriptRoot/_common.ps1"

if (-not $Ports) { $Ports = $LOG_PORTS }

Section "Ports"
$open = Open-LogPorts $Ports
if ($open.Count -eq 0) { Fail "no log port could be opened"; exit 1 }

if ($Reset) {
    Section "Reset"
    & (Get-ProgrammerCli) -c port=SWD mode=UR -rst 2>&1 |
        Select-String -Pattern "Error|Reset" | ForEach-Object { Write-Host $_ }
}

Section $(if ($Seconds -gt 0) { "Capturing ${Seconds}s" } else { "Streaming - Ctrl-C to stop" })

# Streaming mode prints as it arrives so a long soak is watchable; timed mode
# reuses the shared drain so its output matches what flash-bootloader.ps1 shows.
if ($Seconds -gt 0) {
    $buf = Read-LogPorts $open $Seconds
    foreach ($k in $buf.Keys) {
        Section "$k  ($($buf[$k].Length) bytes)"
        if ($buf[$k].Length -gt 0) { Write-Host $buf[$k] }
    }
} else {
    try {
        while ($true) {
            foreach ($k in @($open.Keys)) {
                try {
                    if ($open[$k].BytesToRead -gt 0) {
                        $chunk = $open[$k].ReadExisting()
                        if ($open.Count -gt 1) { Write-Host -NoNewline "[$k] " }
                        Write-Host -NoNewline $chunk
                    }
                } catch {}
            }
            Start-Sleep -Milliseconds 60
        }
    } finally {
        foreach ($k in @($open.Keys)) { try { $open[$k].Close() } catch {} }
        Write-Host ""
    }
}
