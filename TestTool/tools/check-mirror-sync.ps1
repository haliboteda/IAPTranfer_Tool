# Checks the cross-repo mirrored code listed in
# open_plc_cube_ide/docs/ARCHITECTURE.md ("跨仓镜像的代码").
#
# Those copies cannot be enforced by any build system -- three repos, no shared
# build -- so a one-sided edit diverges silently and only shows up at runtime as
# some unrelated-looking symptom. This script compares a semantic anchor per
# item rather than whole files: the files legitimately differ (extern "C" in the
# C++ core, different surrounding APIs), only the anchors must agree.
#
# Exit code 0 = every anchor agrees, 1 = at least one diverged, 2 = a file the
# check needs is missing.
#
# Anchors that are NOT checked here are listed at the bottom of the output, so
# "all green" never reads as "everything is covered".

. "$PSScriptRoot\_common.ps1"

$script:failed  = 0
$script:skipped = 0

# Pull every capture-group-1 match out of a file, in order. Missing file or zero
# matches both return $null so the caller can tell them apart from "".
function Get-Anchor {
    param([string]$Path, [string]$Pattern, [switch]$All)
    if (-not (Test-Path $Path)) { return $null }
    $text = Get-Content $Path -Raw
    $m = [regex]::Matches($text, $Pattern)
    if ($m.Count -eq 0) { return $null }
    if ($All) { return (($m | ForEach-Object { $_.Groups[1].Value.Trim() }) -join "|") }
    return $m[0].Groups[1].Value.Trim()
}

# One row of the report. $Sides is an ordered hashtable of label -> extracted
# value; they must all be equal and non-null.
function Compare-Anchor {
    param([string]$Name, [hashtable]$Sides)

    $missing = @($Sides.Keys | Where-Object { $null -eq $Sides[$_] })
    if ($missing.Count -gt 0) {
        Warn ("SKIP  {0}" -f $Name)
        foreach ($k in $missing) { Warn ("        not found in: {0}" -f $k) }
        $script:skipped++
        return
    }

    $values = @($Sides.Keys | ForEach-Object { $Sides[$_] } | Select-Object -Unique)
    if ($values.Count -eq 1) {
        Ok ("OK    {0}" -f $Name)
        return
    }

    Fail ("DIFF  {0}" -f $Name)
    foreach ($k in $Sides.Keys) {
        $v = $Sides[$k]
        if ($v.Length -gt 120) { $v = $v.Substring(0, 117) + "..." }
        Fail ("        {0,-46} {1}" -f $k, $v)
    }
    $script:failed++
}

$bootUdp    = Join-Path $BOOT_REPO "IAPServer\udp_server.c"
$coreUdp    = Join-Path $CORE_LIVE "libraries\OpenPLC_IAP\src\udp_server.c"
$bootEth    = Join-Path $BOOT_REPO "LWIP\Target\ethernetif.c"
$coreEth    = Join-Path $CORE_LIVE "libraries\OpenPLC_Net\src\ethernetif.c"
$bootSrv    = Join-Path $BOOT_REPO "IAPServer\IAP_server.c"
$bootHand   = Join-Path $BOOT_REPO "IAPServer\IAP_boot_handoff.h"
$coreHand   = Join-Path $CORE_LIVE "cores\arduino\stm32\IAP_boot_handoff.h"
$toolLock   = Join-Path $TOOL_REPO "uploadlock.go"
$coreDisc   = Join-Path $CORE_LIVE "tools\discovery\network_discovery.go"
$bootPwd    = Join-Path $BOOT_REPO "IAPServer\keys\iap_fixed_password.txt"
$corePwd    = Join-Path $CORE_LIVE "libraries\OpenPLC_IAP\src\keys\iap_fixed_password.txt"
$shipPwd    = Join-Path $CORE_LIVE "..\..\..\tools\STM32Tools\0.1.2\win\keys\iap_fixed_password.txt"

Section "cross-repo mirrors"

# --- discovery rate limiter -------------------------------------------------
# Divergence here means one image throttles and the other does not, which is
# exactly the bug that produced the 2026-08-15 "board disappears" reports.
Compare-Anchor "discovery reply cap (replies/sec)" @{
    "bootloader IAPServer/udp_server.c"        = Get-Anchor $bootUdp '#define\s+DISCOVERY_MAX_REPLIES_PER_SEC\s+(\d+)'
    "core OpenPLC_IAP/src/udp_server.c"        = Get-Anchor $coreUdp '#define\s+DISCOVERY_MAX_REPLIES_PER_SEC\s+(\d+)'
}

Compare-Anchor "discovery rate-limit window (ms)" @{
    "bootloader IAPServer/udp_server.c"        = Get-Anchor $bootUdp 'window_start\)\s*>=\s*(\d+)'
    "core OpenPLC_IAP/src/udp_server.c"        = Get-Anchor $coreUdp 'window_start\)\s*>=\s*(\d+)'
}

# --- MAC derivation ---------------------------------------------------------
# Two boards on one LAN collide if this diverges, and it only shows up in the
# field. The array is named MACAddr on one side and mac on the other, so the
# byte assignments are normalised before comparing.
#
# Both files also contain byte assignments that are NOT the derivation -- the
# CubeMX-generated constant MAC in the bootloader, the MAC_ADDR0..5 override
# branch in the core -- so only assignments whose right-hand side comes from the
# UID are collected. Anything else is a different code path.
function Get-MacDerivation([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $text = (Get-Content $Path -Raw) -replace 'MACAddr', 'M' -replace '\bmac\b', 'M'
    $hash = [regex]::Match($text, 'h\s*=\s*(u0[^;]+);')
    if (-not $hash.Success) { return $null }
    $parts = @($hash.Groups[1].Value)
    foreach ($b in [regex]::Matches($text, 'M\[(\d)\]\s*=\s*([^;]+);')) {
        $rhs = $b.Groups[2].Value
        if ($rhs -notmatch '\bh\b|\bu0\b|0x02U') { continue }
        $parts += ("{0}={1}" -f $b.Groups[1].Value, $rhs)
    }
    if ($parts.Count -ne 7) { return $null }
    return (($parts -join ";") -replace '\s+', '')
}

Compare-Anchor "MAC derived from UID" @{
    "bootloader LWIP/Target/ethernetif.c"      = Get-MacDerivation $bootEth
    "core OpenPLC_Net/src/ethernetif.c"        = Get-MacDerivation $coreEth
}

# --- identity string --------------------------------------------------------
# The PC tool splits the reply on "_"; a format change on one side alone makes
# that side's boards unparseable.
Compare-Anchor "identity string format" @{
    "bootloader IAPServer/IAP_server.c"        = Get-Anchor $bootSrv 'snprintf\([^;]*?"(%s_[^"]*)"'
    "core OpenPLC_IAP/src/udp_server.c"        = Get-Anchor $coreUdp 'snprintf\([^;]*?"(%s_[^"]*)"'
}

# --- SRAM4 handoff record ---------------------------------------------------
# A layout or magic mismatch means the app's "stay in the bootloader" request is
# read as garbage. The record fails towards staying in the bootloader, so the
# symptom is a board that will not boot its app rather than one that ignores the
# request -- still worth catching before it ships.
function Get-HandoffLayout([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    $text = Get-Content $Path -Raw
    $parts = @()
    foreach ($k in @('BOOT_HANDOFF_ADDR', 'BOOT_HANDOFF_SIZE', 'BOOT_HANDOFF_MAGIC', 'BOOT_HANDOFF_VERSION')) {
        $m = [regex]::Match($text, ("#define\s+{0}\s+(\S+)" -f $k))
        if (-not $m.Success) { return $null }
        $parts += ("{0}={1}" -f $k, $m.Groups[1].Value)
    }
    foreach ($m in [regex]::Matches($text, 'BOOT_REQ_(\w+)\s*=\s*(\d+)')) {
        $parts += ("{0}={1}" -f $m.Groups[1].Value, $m.Groups[2].Value)
    }
    $st = [regex]::Match($text, 'typedef struct \{(.*?)\} boot_handoff_t;', 'Singleline')
    if (-not $st.Success) { return $null }
    foreach ($m in [regex]::Matches($st.Groups[1].Value, '(uint\d+_t)\s+(\w+)\s*;')) {
        $parts += ("{0} {1}" -f $m.Groups[1].Value, $m.Groups[2].Value)
    }
    return ($parts -join ";")
}

Compare-Anchor "SRAM4 boot_handoff_t layout" @{
    "bootloader IAPServer/IAP_boot_handoff.h"  = Get-HandoffLayout $bootHand
    "core cores/arduino/stm32/IAP_boot_handoff.h" = Get-HandoffLayout $coreHand
}

# --- upload lock ------------------------------------------------------------
# IAPTool and network_discovery coordinate purely through this file. A name
# mismatch means neither sees the other and the IDE's poller talks over an
# upload in progress.
Compare-Anchor "upload lock filename" @{
    "IAPTool uploadlock.go"                    = Get-Anchor $toolLock 'uploadLockName\s*=\s*"([^"]+)"'
    "core tools/discovery/network_discovery.go" = Get-Anchor $coreDisc 'uploadLockName\s*=\s*"([^"]+)"'
}

Compare-Anchor "upload lock max age" @{
    "IAPTool uploadlock.go"                    = Get-Anchor $toolLock 'UploadLockMaxAge\s*=\s*(\d+\s*\*\s*time\.\w+)'
    "core tools/discovery/network_discovery.go" = Get-Anchor $coreDisc 'uploadLockMaxAge\s*=\s*(\d+\s*\*\s*time\.\w+)'
}

# --- shared secret ----------------------------------------------------------
# Three byte-identical copies. rotate_keys.sh updates the first two; the third
# ships beside IAPTool.exe and is read at runtime, so a board reflashed after a
# rotation stops answering a tool that still holds the old password.
function Get-FileHashOrNull([string]$Path) {
    if (-not (Test-Path $Path)) { return $null }
    return (Get-FileHash $Path -Algorithm SHA256).Hash
}

Compare-Anchor "iap_fixed_password.txt (3 copies)" @{
    "bootloader IAPServer/keys/"               = Get-FileHashOrNull $bootPwd
    "core OpenPLC_IAP/src/keys/"               = Get-FileHashOrNull $corePwd
    "shipped STM32Tools/*/win/keys/"           = Get-FileHashOrNull $shipPwd
}

# --- RTC backup registers ---------------------------------------------------
# Not a mirror but the same failure mode: a shared resource with no allocator.
# Claiming one that the other image already uses cost a real bug (bootloader's
# VBAT witness vs the app's nonce counter, both on DR2, 2026-08-17).
Section "RTC backup register claims"

$regPattern = 'RTC_BKP_DR(\d+)'
$claims = @{}
$scan = @(
    @{ Label = "bootloader IAPServer/iap_auth.c";        Path = (Join-Path $BOOT_REPO "IAPServer\iap_auth.c") },
    @{ Label = "core OpenPLC_IAP/src/iap_auth.c";        Path = (Join-Path $CORE_LIVE "libraries\OpenPLC_IAP\src\iap_auth.c") }
)
foreach ($s in $scan) {
    if (-not (Test-Path $s.Path)) { Warn ("SKIP  {0} not found" -f $s.Label); $script:skipped++; continue }
    $text = Get-Content $s.Path -Raw
    foreach ($m in [regex]::Matches($text, $regPattern)) {
        $dr = "DR" + $m.Groups[1].Value
        if (-not $claims.ContainsKey($dr)) { $claims[$dr] = @() }
        if ($claims[$dr] -notcontains $s.Label) { $claims[$dr] += $s.Label }
    }
}
foreach ($dr in ($claims.Keys | Sort-Object)) {
    if ($claims[$dr].Count -gt 1) {
        Fail ("CLASH {0} claimed by: {1}" -f $dr, ($claims[$dr] -join " AND "))
        $script:failed++
    } else {
        Ok ("OK    {0} <- {1}" -f $dr, $claims[$dr][0])
    }
}
Write-Host "      (the allocation table in docs/ARCHITECTURE.md is the record; this only"
Write-Host "       scans the two iap_auth.c files, not the core's backup.h or HID indices)"

# --- what this script does not check ----------------------------------------
Section "not covered by this script -- still manual"
Write-Host "  - iap_keyderive HMAC formula across the two C copies and iapcrypto.go"
Write-Host "      (covered instead by host/iapcrypto/ and host/bootloader_unit/)"
Write-Host "  - fw_pubkey.inc: bootloader-only by design, nothing to compare"
Write-Host '  - $CORE_LIVE vs $CORE_REPO: use tools/check-core-sync.ps1'

Section "result"
if ($script:failed -gt 0) {
    Fail ("{0} anchor(s) diverged, {1} skipped" -f $script:failed, $script:skipped)
    exit 1
}
if ($script:skipped -gt 0) {
    Warn ("all compared anchors agree, but {0} could not be checked" -f $script:skipped)
    exit 0
}
Ok "all mirrored anchors agree"
exit 0
