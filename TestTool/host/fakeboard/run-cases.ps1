# Drives the real IAPTool.exe against fake_board.py and checks the decision it
# makes about the firmware signing key -- before any firmware is sent.
#
# Why this cannot be done on a real board: the six outcomes below differ only in
# which key the bootloader was compiled with, and in whether a private key or a
# .sig file is present on the host. Reproducing them on hardware means
# reflashing the bootloader with a different key for each case. Here it is a
# command-line argument.
#
# What is under test is IAPTool, not the device. fake_board.py verifies nothing;
# device-side verification is covered by S1 against real hardware.
#
#   .\run-cases.ps1              run all six
#   .\run-cases.ps1 -Keep        keep the scratch directory for inspection
#
# Exit 0 = all six matched, 1 = at least one did not, 2 = prerequisites missing.

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

# The port IAPTool will dial. Read it from the same config the tool reads, so
# the two cannot drift apart.
$port = "56865"
$cfgJson = Join-Path $TOOL_REPO "local_config.json"
if (Test-Path $cfgJson) {
    $j = Get-Content $cfgJson -Raw | ConvertFrom-Json
    if ($j.server_port) { $port = $j.server_port }
}

# The "good" key is whichever one the bootloader was built to trust: parse the
# byte array the build #includes, rather than keeping a second copy here that
# would go stale the first time anyone rotates keys.
$incPath = Join-Path $BOOT_REPO "IAPServer\keys\fw_pubkey.inc"
if (-not (Test-Path $incPath)) { Fail "not found: $incPath"; exit 2 }
$goodHex = (([regex]::Matches((Get-Content $incPath -Raw), '0x([0-9a-fA-F]{2})') |
              ForEach-Object { $_.Groups[1].Value }) -join "").ToLower()
if ($goodHex.Length -ne 128) { Fail "fw_pubkey.inc parsed to $($goodHex.Length) hex chars, expected 128"; exit 2 }

$scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("fakeboard-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
New-Item -ItemType Directory -Path $scratch | Out-Null
Write-Host "scratch: $scratch"

# IAPTool resolves local_config.json and its fallback keys/ directory relative
# to its own executable, not the working directory. The "host has no private
# key" cases are therefore unreachable while running the checked-out copy --
# omitting --key just falls back to the signing_key in the repo's config, which
# is how the first version of this script silently tested nothing. Run a copy in
# a scratch directory whose config names no key instead.
$iapRun = Join-Path $scratch "IAPTool.exe"
Copy-Item $iapTool $iapRun
# WriteAllText, not Set-Content -Encoding utf8: on PowerShell 5.1 the latter
# emits a BOM, and Go's json.Unmarshal rejects it -- IAPTool then exits before
# doing anything, which looks like the tool is broken rather than the config.
$scratchCfg = @{
    server_port   = $port
    signing_key   = ""
    password_file = (Join-Path $BOOT_REPO "IAPServer\keys\iap_fixed_password.txt")
} | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $scratch "local_config.json"), $scratchCfg)

# A second, unrelated key pair for the mismatch cases. IAPTool can make one, so
# nothing has to be committed and nothing depends on openssl being installed.
Push-Location $scratch
$genOut = & $iapRun genkey "other_key" 2>&1 | Out-String -Width 4096
Pop-Location
$badHex = (([regex]::Matches($genOut, '0x([0-9a-fA-F]{2})') |
             ForEach-Object { $_.Groups[1].Value }) -join "").ToLower()
if ($badHex.Length -ne 128) { Fail "IAPTool genkey output parsed to $($badHex.Length) hex chars"; exit 2 }

$goodKey = Join-Path $BOOT_REPO "IAPServer\keys\fw_signing_key.TEST_ONLY.pem"
if (-not (Test-Path $goodKey)) { Fail "not found: $goodKey"; exit 2 }
$otherKey = Join-Path $scratch "other_key.pem"

# Any bytes will do -- nothing on either side inspects the image content in the
# phase under test. Fixed content keeps the run reproducible.
$binPath = Join-Path $scratch "app.bin"
$bytes = New-Object byte[] 2048
for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [byte](($i * 31 + 7) % 256) }
[System.IO.File]::WriteAllBytes($binPath, $bytes)

# The board answers getversion with "3", so an unversioned upload would stop to
# ask about a downgrade and hang with no console to answer on.
$encoded = (& $iapRun version "9.9.9" 2>&1 | Out-String -Width 4096).Trim()
$verNum = ([regex]::Match($encoded, '(\d+)\s*$')).Groups[1].Value
if (-not $verNum) { Fail "cannot encode a version via IAPTool version"; exit 2 }

# id, board's pubkey, --key to pass (or ""), whether a .sig should exist, expected line
$cases = @(
    @{ Id = "key-match";     Pub = $goodHex; Key = $goodKey;  Sig = $false; Expect = "Signing key matches this board" }
    @{ Id = "key-mismatch";  Pub = $badHex;  Key = $goodKey;  Sig = $false; Expect = "verifies against a different signing key" }
    @{ Id = "old-bootload";  Pub = "unknown"; Key = $goodKey; Sig = $false; Expect = "skipping key match check" }
    @{ Id = "sig-match";     Pub = $goodHex; Key = "";        Sig = $true;  Expect = "Signature verifies against this board" }
    @{ Id = "sig-mismatch";  Pub = $badHex;  Key = "";        Sig = $true;  Expect = "does not verify against this board" }
    @{ Id = "nothing";       Pub = $goodHex; Key = "";        Sig = $false; Expect = "no signing key found and no signature" }
)

$fakeBoard = Join-Path $PSScriptRoot "fake_board.py"
$sigPath = [System.IO.Path]::ChangeExtension($binPath, ".sig")
$failed = 0

foreach ($c in $cases) {
    Section "$($c.Id)  -- expecting: $($c.Expect)"

    # A .sig produced by the good key: the sig-mismatch case is "the host has a
    # valid signature, but this board trusts someone else", which is a different
    # failure from a corrupt signature.
    if (Test-Path $sigPath) { Remove-Item $sigPath -Force }
    if ($c.Sig) {
        & $iapRun sign $binPath $goodKey --version=$verNum 2>&1 | Out-Null
        if (-not (Test-Path $sigPath)) { Fail "could not produce a .sig"; $failed++; continue }
    }

    $boardLog = Join-Path $scratch "board_$($c.Id).log"
    $board = Start-Process -FilePath "python" `
        -ArgumentList @($fakeBoard, $c.Pub, "30", "--port", $port) `
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

    # Out-String wraps at the console width by default, which splits the very
    # lines being matched. -Width keeps them intact.
    $args = @("ether", $binPath, "127.0.0.1", "--version=$($verNum)", "--downgrade=allow")
    if ($c.Key) { $args += "--key=$($c.Key)" }
    $out = & $iapRun @args 2>&1 | Out-String -Width 4096

    try { Stop-Process -Id $board.Id -Force -ErrorAction Stop } catch {}

    if ($out -match [regex]::Escape($c.Expect)) {
        Ok "PASS"
    } else {
        Fail "FAIL - expected line not found. IAPTool said:"
        ($out -split "`r?`n" | Where-Object { $_.Trim() }) | ForEach-Object { Write-Host "    $_" }
        $failed++
    }
}

if (-not $Keep) { Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue }
else { Write-Host "kept: $scratch" }

Section "result"
if ($failed -gt 0) { Fail "$failed of $($cases.Count) case(s) failed"; exit 1 }
Ok "all $($cases.Count) key-match cases behaved as expected"
exit 0
