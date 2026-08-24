# OW1 -- claim a board for a signing key, and check it took (requirement C10).
#
#   .\run-takeown.ps1                 claim with a freshly generated key
#   .\run-takeown.ps1 -Key <128hex>   claim with a specific key
#   .\run-takeown.ps1 -ExpectRefused  the board should say no (negative case)
#
# ⚠️ THIS NEEDS SOMEBODY AT THE BOARD, and that is the whole point. takeown is
# gated on BOOT0 having been held through the startup window: the first claim
# carries no signature -- there is no owner yet to sign it -- so physical
# presence is the only gate there can be. See docs/design/OWNERSHIP.md.
#
# Before running: press RESET, then hold BOOT0 until the relays finish clicking
# and let go. The board should be sitting in "UPLOAD Mod ... (BOOT0 held)".
#
# ⚠️ RECOVERY: claiming is meant to be hard to undo. Factory reset (M1 step 6)
# is not implemented yet, so the only way back is to reflash the bootloader over
# ST-Link -- the owner records live in the bootloader's own sector, so erasing
# it to write the bootloader takes them with it:
#
#     .\flash-bootloader.ps1
#
# Exit 0 = the board ended up in the expected state, 1 = it did not, 2 = setup.

param(
    [string]$Ip,
    [string]$Port = "56865",
    [string]$Key,
    [switch]$ExpectRefused
)

. "$PSScriptRoot/_common.ps1"

if (-not $Ip) { $Ip = $BOARD_IP }
if (-not $Ip) { Fail "need -Ip (or set BOARD_IP in config/machine.ps1)"; exit 2 }

# One request, one reply. The board serves a single client, so each exchange
# opens and closes its own connection rather than holding the port.
function Send-Command([string]$cmd, [int]$timeoutMs = 8000) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect($Ip, [int]$Port)
        $s = $client.GetStream()
        $s.ReadTimeout = $timeoutMs
        $bytes = [Text.Encoding]::ASCII.GetBytes($cmd + "`n")
        $s.Write($bytes, 0, $bytes.Length)
        Start-Sleep -Milliseconds 400
        $buf = New-Object byte[] 4096
        $n = 0
        try { $n = $s.Read($buf, 0, $buf.Length) } catch {}
        return [Text.Encoding]::ASCII.GetString($buf, 0, $n).Trim()
    } catch {
        return "<<no reply: $($_.Exception.Message)>>"
    } finally {
        $client.Close()
    }
}

# The hands-on prompt. Same shape every time, because the operator scans for the
# icon rather than reading the paragraph. Reasons go OUTSIDE the box; the box
# holds the action and nothing else.
function Banner([string[]]$Lines) {
    Write-Host ""
    Write-Host ("=" * 68)
    for ($i = 0; $i -lt $Lines.Count; $i++) {
        $prefix = if ($i -eq 0) { "  " + [char]0xD83C + [char]0xDF4D + " " } else { "     " }
        Write-Host ($prefix + $Lines[$i]) -ForegroundColor Yellow
    }
    Write-Host ("=" * 68)
    Write-Host ""
}

# Said at run time, not only in the comment at the top of this file: the board
# has to ALREADY be in this state before the first command goes out, and a
# requirement nobody sees is a requirement nobody meets.
Banner @(
    "PRESS RESET, THEN HOLD BOOT0 UNTIL THE RELAYS STOP CLICKING.",
    "Let go. The board should print: UPLOAD Mod ... (BOOT0 held)"
)

Write-Host "  Nothing to confirm -- if BOOT0 was not held, the board answers Refused"
Write-Host "  and this script says so. That refusal IS the check."
Write-Host ""
Write-Host "  Why physical presence: the first claim carries no signature (there is no"
Write-Host "  owner yet to sign it), so a button is the only gate there can be."
Write-Host ""

Section "before"
$was = Send-Command "getpubkey"
Write-Host "  getpubkey: $was"
if ($was -notmatch '^[0-9a-fA-F]{128}$') {
    Fail "the board did not answer getpubkey with a key -- is it in the bootloader?"
    exit 2
}

if (-not $Key) {
    Section "generating a key to claim with"
    $iap = Get-GoBin "IAPTool"
    if (-not (Test-Path $iap)) { Fail "IAPTool not built"; exit 2 }
    $scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("takeown-" + [guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $scratch | Out-Null
    Push-Location $scratch
    $out = & $iap genkey "owner_key" 2>&1 | Out-String -Width 4096
    Pop-Location
    $Key = (([regex]::Matches($out, '0x([0-9a-fA-F]{2})') |
             ForEach-Object { $_.Groups[1].Value }) -join "").ToLower()
    if ($Key.Length -ne 128) { Fail "genkey produced $($Key.Length) hex chars"; exit 2 }
    Write-Host "  private key kept at: $scratch\owner_key.pem"
    # Plain ASCII: the console codepage mangles anything else, and a warning
    # that renders as mojibake is a warning nobody reads.
    Write-Host "  NOTE: from now on that key is the only one that can sign firmware"
    Write-Host "        this board will run. It is in a temp directory - move it."
}
Write-Host "  claiming with: $($Key.Substring(0,32))..."

Section "takeown"
$reply = Send-Command ("takeown " + $Key)
Write-Host "  reply: $reply"

Section "after"
$now = Send-Command "getpubkey"
Write-Host "  getpubkey: $now"

Section "result"
if ($ExpectRefused) {
    if ($reply -match "Refused") {
        Ok "refused, as expected"
        if ($now -ne $was) { Fail "  but the trusted key changed anyway!"; exit 1 }
        Ok "  and the trusted key is unchanged"
        exit 0
    }
    Fail "expected a refusal, got: $reply"
    exit 1
}

if ($reply -notmatch "OK") {
    Fail "takeown did not succeed: $reply"
    Fail "(BOOT0 must have been held through the startup window of THIS boot)"
    exit 1
}
if ($now -ne $Key) {
    Fail "the board reports a different key than the one claimed"
    Fail "  claimed  $Key"
    Fail "  reports  $now"
    exit 1
}
Ok "claimed: the board now reports the new key as its root"
Write-Host ""
Write-Host "Next: reset the board. The published-root warning should be gone and the"
Write-Host "boot log should say 'claimed at generation 1'."
Write-Host "To undo: .\flash-bootloader.ps1  (erases the sector the records live in)"
exit 0
