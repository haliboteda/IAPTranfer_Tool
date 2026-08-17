# DG1 -- the downgrade guard. Drives the real IAPTool.exe against fake_board.py
# and checks what it does when the image is older than what the device reports.
#
# Covers requirement C6. Until this existed, --downgrade had no test at all: the
# only evidence it worked was that somebody once watched it print the word
# "refused".
#
# Two assertions per case, and the second is the point:
#
#   1. what IAPTool said on stdout, and
#   2. whether the stand-in board was ever sent a "flash" command.
#
# A tool that printed "Downgrade refused" and then uploaded anyway would sail
# through a log-only check. C6's actual claim is that the installed app is not
# touched, so the board's own log is what has to prove it.
#
# Why no real board is needed: every branch under test lives in IAPTool
# (auth.go confirmDowngradeIfNeeded). The device contributes exactly one thing,
# its answer to "getversion", which fake_board.py serves from --fwver. Doing
# this on hardware would mean flashing a real older app to set the board up for
# each case. Device-side behaviour is S1/S2/G1 against hardware.
#
#   .\run-downgrade.ps1              run all five
#   .\run-downgrade.ps1 -Keep        keep the scratch directory for inspection
#
# Exit 0 = all five matched, 1 = at least one did not, 2 = prerequisites missing.

param([switch]$Keep)

. "$PSScriptRoot\..\..\tools\_common.ps1"

foreach ($need in @("python", "go")) {
    if (-not (Get-Command $need -ErrorAction SilentlyContinue)) {
        Fail "$need is not on PATH"; exit 2
    }
}

$iapTool = Join-Path $TOOL_REPO "Output\windows\IAPTool.exe"
if (-not (Test-Path $iapTool)) {
    Warn "IAPTool.exe not built, building it now"
    Push-Location $TOOL_REPO
    go build -o "Output/windows/IAPTool.exe" .
    $rc = $LASTEXITCODE
    Pop-Location
    if ($rc -ne 0 -or -not (Test-Path $iapTool)) { Fail "cannot build IAPTool"; exit 2 }
}

# Same port resolution as run-cases.ps1: read it from the config the tool reads,
# so the two cannot drift apart.
$port = "56865"
$cfgJson = Join-Path $TOOL_REPO "local_config.json"
if (Test-Path $cfgJson) {
    $j = Get-Content $cfgJson -Raw | ConvertFrom-Json
    if ($j.server_port) { $port = $j.server_port }
}

# The board has to answer getpubkey with the key IAPTool signs with: the version
# query happens on the same preflight connection, *after* the key match check,
# and a mismatch aborts before getversion is ever sent. Parse it out of the file
# the bootloader compiles in rather than keeping a second copy here.
$incPath = Join-Path $BOOT_REPO "IAPServer\keys\fw_pubkey.inc"
if (-not (Test-Path $incPath)) { Fail "not found: $incPath"; exit 2 }
$goodHex = (([regex]::Matches((Get-Content $incPath -Raw), '0x([0-9a-fA-F]{2})') |
              ForEach-Object { $_.Groups[1].Value }) -join "").ToLower()
if ($goodHex.Length -ne 128) { Fail "fw_pubkey.inc parsed to $($goodHex.Length) hex chars, expected 128"; exit 2 }

$goodKey = Join-Path $BOOT_REPO "IAPServer\keys\fw_signing_key.TEST_ONLY.pem"
if (-not (Test-Path $goodKey)) { Fail "not found: $goodKey"; exit 2 }

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("downgrade-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $scratch | Out-Null
Write-Host "scratch: $scratch"

# Run a copy from a scratch directory, for the same reason run-cases.ps1 does:
# IAPTool resolves local_config.json relative to its own executable. Here it
# also keeps the run from depending on whatever the checked-out config happens
# to say. WriteAllText, not Set-Content -Encoding utf8 -- PS 5.1 adds a BOM and
# Go's json.Unmarshal rejects it.
$iapRun = Join-Path $scratch "IAPTool.exe"
Copy-Item $iapTool $iapRun
$scratchCfg = @{
    server_port   = $port
    signing_key   = ""
    password_file = (Join-Path $BOOT_REPO "IAPServer\keys\iap_fixed_password.txt")
} | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $scratch "local_config.json"), $scratchCfg)

# Encode the three versions through IAPTool itself. Hardcoding 0x00090000 here
# would be a second copy of encodeSemver, and the packed-byte layout is exactly
# the kind of thing that gets changed once and forgotten in one place.
function Encode-Version([string]$semver) {
    $out = (& $iapRun version $semver 2>&1 | Out-String -Width 4096).Trim()
    $n = ([regex]::Match($out, '(\d+)\s*$')).Groups[1].Value
    if (-not $n) { Fail "cannot encode version $semver via IAPTool: $out"; exit 2 }
    return $n
}
$vOlder = Encode-Version "0.9.0"
$vSame  = Encode-Version "1.0.0"
$vNewer = Encode-Version "1.0.1"
Write-Host "versions: older=$vOlder  installed=$vSame  newer=$vNewer"
if ([uint32]$vOlder -ge [uint32]$vSame -or [uint32]$vNewer -le [uint32]$vSame) {
    Fail "version encoding does not order as expected -- the cases below would prove nothing"
    exit 2
}

# Any bytes will do; nothing on either side inspects the image content. Fixed
# content keeps the run reproducible.
$binPath = Join-Path $scratch "app.bin"
$bytes = New-Object byte[] 2048
for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [byte](($i * 17 + 3) % 256) }
[System.IO.File]::WriteAllBytes($binPath, $bytes)

# Flash = did the board see a "flash" command. $false means the upload never
# started, which is what "the installed app is untouched" reduces to here.
#  NoConsole: hand IAPTool a piped stdin so "ask" hits its no-terminal branch
#  deterministically, instead of depending on how this script was launched.
$cases = @(
    @{ Id = "refuse-older"; Ver = $vOlder; Mode = "refuse"; NoConsole = $false; Flash = $false
       Expect = "Downgrade refused by --downgrade=refuse." }
    @{ Id = "allow-older";  Ver = $vOlder; Mode = "allow";  NoConsole = $false; Flash = $true
       Expect = "Downgrade allowed by --downgrade=allow." }
    @{ Id = "ask-no-console"; Ver = $vOlder; Mode = "ask";  NoConsole = $true;  Flash = $false
       Expect = "Cannot ask: no interactive terminal" }
    # The two reverse cases. "refuse" must not block anything that is not a
    # downgrade -- and same-version is the boundary the >= comparison turns on,
    # so an off-by-one there would lock out every re-flash of the same build.
    @{ Id = "same-version"; Ver = $vSame;  Mode = "refuse"; NoConsole = $false; Flash = $true
       Expect = "File transfer complete." }
    @{ Id = "newer";        Ver = $vNewer; Mode = "refuse"; NoConsole = $false; Flash = $true
       Expect = "File transfer complete." }
)

$fakeBoard = Join-Path $PSScriptRoot "fake_board.py"
$failed = 0

foreach ($c in $cases) {
    Section "$($c.Id)  -- expecting: $($c.Expect)"

    $boardLog = Join-Path $scratch "board_$($c.Id).log"
    $board = Start-Process -FilePath "python" `
        -ArgumentList @($fakeBoard, $goodHex, "30", "--port", $port, "--fwver", $vSame) `
        -RedirectStandardOutput $boardLog -RedirectStandardError "$boardLog.err" `
        -PassThru -WindowStyle Hidden

    # Wait for the listener rather than sleeping a fixed amount.
    $up = $false
    for ($i = 0; $i -lt 100; $i++) {
        try {
            $probe = New-Object System.Net.Sockets.TcpClient
            $probe.Connect("127.0.0.1", [int]$port)
            $probe.Close(); $up = $true; break
        } catch { Start-Sleep -Milliseconds 100 }
    }
    if (-not $up) {
        Fail "fake board never listened on $port"
        if (Test-Path $boardLog) { Get-Content $boardLog | ForEach-Object { Write-Host "  $_" } }
        $failed++
        try { Stop-Process -Id $board.Id -Force -ErrorAction Stop } catch {}
        continue
    }

    # -Width keeps the lines being matched from being wrapped by Out-String.
    $args = @("ether", $binPath, "127.0.0.1", "--key=$goodKey",
              "--version=$($c.Ver)", "--downgrade=$($c.Mode)")
    if ($c.NoConsole) {
        $out = "" | & $iapRun @args 2>&1 | Out-String -Width 4096
    } else {
        $out = & $iapRun @args 2>&1 | Out-String -Width 4096
    }

    try { Stop-Process -Id $board.Id -Force -ErrorAction Stop } catch {}

    $boardSaw = ""
    if (Test-Path $boardLog) { $boardSaw = Get-Content $boardLog -Raw }
    $flashed = $boardSaw -match "CMD 'flash"

    $why = @()
    if ($out -notmatch [regex]::Escape($c.Expect)) { $why += "IAPTool never said: $($c.Expect)" }
    if ($flashed -ne $c.Flash) {
        if ($c.Flash) { $why += "board was never sent a flash command, but this upload should have gone through" }
        else          { $why += "board WAS sent a flash command -- the installed app would have been overwritten" }
    }
    # The positive cases only count if the image actually landed; "flash" was
    # sent tells us the guard let go, not that the transfer survived.
    if ($c.Flash -and $boardSaw -notmatch "IMAGE FULLY RECEIVED") {
        $why += "board never reported the image fully received"
    }

    if ($why.Count -eq 0) {
        Ok "PASS"
    } else {
        Fail "FAIL"
        $why | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
        Write-Host "  -- IAPTool said:"
        ($out -split "`r?`n" | Where-Object { $_.Trim() }) | ForEach-Object { Write-Host "    $_" }
        Write-Host "  -- board saw:"
        ($boardSaw -split "`r?`n" | Where-Object { $_.Trim() }) | ForEach-Object { Write-Host "    $_" }
        $failed++
    }
}

if (-not $Keep) { Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue }
else { Write-Host "kept: $scratch" }

Section "result"
if ($failed -gt 0) { Fail "$failed of $($cases.Count) case(s) failed"; exit 1 }
Ok "all $($cases.Count) downgrade cases behaved as expected"
exit 0
