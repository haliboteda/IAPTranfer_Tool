# OW2 -- hand a claimed board to a new owner (requirement C10, M1 step 5).
#
#   .\run-setowner.ps1 -CurrentKey <owner.pem>              new key generated
#   .\run-setowner.ps1 -CurrentKey <owner.pem> -BadSignature  must be refused
#
# Unlike takeown this needs NOBODY at the board: the current owner's signature
# is the authorisation, and handing a board over remotely is a supported case.
# Physical presence gates only the operations with no signature to check --
# the first claim, and factory reset.
#
# What gets signed is the first 76 bytes of the record about to be written:
#
#     type 'O' | slots 5 | format_ver 1 | generation | flags | new public key
#        1          1          2 (LE)      4 (LE)     4 (LE)      64
#
# The generation is inside the signature on purpose: without it a captured
# record could be replayed into a later slot and undo a subsequent handover.
#
# Exit 0 = ended up in the expected state, 1 = did not, 2 = setup problem.

param(
    [Parameter(Mandatory = $true)][string]$CurrentKey,
    [string]$Ip,
    [string]$Port = "56865",
    [string]$NewKey,
    [switch]$BadSignature
)

. "$PSScriptRoot\_common.ps1"

if (-not $Ip) { $Ip = $BOARD_IP }
if (-not $Ip) { Fail "need -Ip (or set BOARD_IP in config/machine.ps1)"; exit 2 }
if (-not (Test-Path $CurrentKey)) { Fail "no such key: $CurrentKey"; exit 2 }

$iap = Join-Path $TOOL_REPO "Output\windows\IAPTool.exe"
if (-not (Test-Path $iap)) { Fail "IAPTool not built"; exit 2 }

function Send-Command([string]$cmd, [int]$timeoutMs = 8000) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect($Ip, [int]$Port)
        $s = $client.GetStream()
        $s.ReadTimeout = $timeoutMs
        $b = [Text.Encoding]::ASCII.GetBytes($cmd + "`n")
        $s.Write($b, 0, $b.Length)
        Start-Sleep -Milliseconds 400
        $buf = New-Object byte[] 4096
        $n = 0
        try { $n = $s.Read($buf, 0, $buf.Length) } catch {}
        return [Text.Encoding]::ASCII.GetString($buf, 0, $n).Trim()
    } catch {
        return "<<no reply: $($_.Exception.Message)>>"
    } finally { $client.Close() }
}

Section "before"
$gen = Send-Command "getowner"
$was = Send-Command "getpubkey"
Write-Host "  generation: $gen"
Write-Host "  root:       $was"
if ($gen -notmatch '^\d+$') { Fail "getowner did not answer with a number: $gen"; exit 2 }
if ([int]$gen -eq 0) {
    Fail "the board is unclaimed - setowner needs an existing owner. Use run-takeown.ps1"
    exit 2
}
$next = [int]$gen + 1

if (-not $NewKey) {
    Section "generating the incoming owner's key"
    $scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("setowner-" + [guid]::NewGuid().ToString("N").Substring(0,8))
    New-Item -ItemType Directory -Path $scratch | Out-Null
    Push-Location $scratch
    $out = & $iap genkey "new_owner" 2>&1 | Out-String -Width 4096
    Pop-Location
    $NewKey = (([regex]::Matches($out, '0x([0-9a-fA-F]{2})') |
                ForEach-Object { $_.Groups[1].Value }) -join "").ToLower()
    if ($NewKey.Length -ne 128) { Fail "genkey produced $($NewKey.Length) hex chars"; exit 2 }
    Write-Host "  private key: $scratch\new_owner.pem"
}

# Build the signed prefix exactly as the bootloader lays the record out.
Section "signed prefix"
$prefix = New-Object byte[] 76
$prefix[0] = 0x4F                                        # type 'O'
$prefix[1] = 5                                           # slots
[BitConverter]::GetBytes([uint16]1).CopyTo($prefix, 2)   # format_ver
[BitConverter]::GetBytes([uint32]$next).CopyTo($prefix, 4)
[BitConverter]::GetBytes([uint32]0).CopyTo($prefix, 8)   # flags
for ($i = 0; $i -lt 64; $i++) {
    $prefix[12 + $i] = [Convert]::ToByte($NewKey.Substring($i * 2, 2), 16)
}
$prefixHex = ($prefix | ForEach-Object { $_.ToString('x2') }) -join ""
Write-Host "  generation $next, 76 bytes"

$sig = (& $iap signraw $prefixHex $CurrentKey 2>&1 | Out-String).Trim()
if ($sig.Length -ne 128) { Fail "signraw returned $($sig.Length) chars: $sig"; exit 2 }

if ($BadSignature) {
    # Flip one bit of a signature that is otherwise perfectly formed. A wrong
    # signature has to be rejected for the same reason a missing one is -- and
    # this is the case a "does it write the record" test would sail past.
    $first = [Convert]::ToByte($sig.Substring(0, 2), 16) -bxor 0x01
    $sig = ("{0:x2}" -f $first) + $sig.Substring(2)
    Warn "  signature deliberately corrupted"
}

Section "setowner"
$reply = Send-Command ("setowner {0} {1} {2}" -f $next, $NewKey, $sig)
Write-Host "  reply: $reply"

Section "after"
$nowGen = Send-Command "getowner"
$now = Send-Command "getpubkey"
Write-Host "  generation: $nowGen"
Write-Host "  root:       $now"

Section "result"
if ($BadSignature) {
    if ($reply -notmatch "Refused") { Fail "expected a refusal, got: $reply"; exit 1 }
    if ($now -ne $was) { Fail "refused, but the root changed anyway!"; exit 1 }
    if ($nowGen -ne $gen) { Fail "refused, but the generation moved!"; exit 1 }
    Ok "refused, and nothing changed"
    exit 0
}
if ($reply -notmatch "OK") { Fail "setowner did not succeed: $reply"; exit 1 }
if ($now -ne $NewKey) {
    Fail "the board reports a different root than the one handed over"
    exit 1
}
if ($nowGen -ne "$next") { Fail "generation is $nowGen, expected $next"; exit 1 }
Ok "handed over: generation $next, and the board reports the new root"
exit 0
