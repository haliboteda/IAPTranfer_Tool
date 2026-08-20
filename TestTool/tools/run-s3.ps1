# S3 -- the boot-time signature check, on its own.
#
#   .\run-s3.ps1 -Bin <app.bin>      corrupt that app on the board, then restore it
#   .\run-s3.ps1 -Bin <app.bin> -NoRestore   leave the board broken (don't)
#
# S1 also exercises the boot-time check, but only as a side effect of an upload
# that failed -- and after SDRAM staging landed, a failed upload never touches
# the application region at all, so S1's boot-time half now proves nothing about
# an application that IS installed. This case is the only one that verifies the
# claim in C3: the application's signature is re-checked on EVERY boot, against
# the bytes actually in flash.
#
# ---------------------------------------------------------------------------
# THIS CASE BREAKS THE BOARD ON PURPOSE. Read this before running it.
# ---------------------------------------------------------------------------
# It rewrites the application region so the installed application no longer
# matches its signature. The board then refuses to boot it until a valid image
# is flashed again. -Bin is that image, and it is flashed back at the end.
#
# Run order is not negotiable: the restore is proven FIRST (the same image is
# flashed and the board is seen to boot it) and only then is anything broken.
# Backwards, a bad restore image leaves a board that will not boot and nothing
# known-good to recover it with.
#
# Why one byte and not an erase: erasing would leave blank flash, which only
# shows the check notices a MISSING application. Flipping one byte in the middle
# shows the hash actually covers the content.
#
# Why erase-then-rewrite the whole image rather than poking the single byte:
# on STM32H7 flash carries ECC per 256-bit word, and programming a word twice
# leaves the stored ECC as the AND of both, which reads back as an uncorrectable
# error and faults the CPU. The bootloader would crash while hashing instead of
# cleanly reporting a bad signature -- a different failure, and a confusing one.
# Writing a full, already-modified image into freshly erased flash avoids it.
#
# Exit 0 = S3 passed and the board is back to normal, 1 = failed, 2 = setup.

param(
    [Parameter(Mandatory = $true)][string]$Bin,
    [string]$Ip,
    [switch]$NoRestore,
    [string[]]$Ports
)

. "$PSScriptRoot/_common.ps1"

if (-not $Ports) { $Ports = $LOG_PORTS }
if (-not $Ip)    { $Ip = $BOARD_IP }
if (-not $Ip)    { Fail "need -Ip (or set BOARD_IP in config/machine.ps1)"; exit 2 }
if (-not (Test-Path $Bin)) { Fail "no such image: $Bin"; exit 2 }

# IAP_APP_ADDRESS = FLASH_BASE | ADDRESS_VECTOR, Core/Inc/usbd_cdc_flash.h:50,58.
$APP_ADDR = "0x08020000"

$cli = Get-ProgrammerCli
$iap = Get-GoBin "IAPTool"
if (-not (Test-Path $iap)) { $iap = Get-IapTool }
if (-not (Test-Path $iap)) { Fail "no IAPTool to restore with"; exit 2 }

$image = [System.IO.File]::ReadAllBytes((Resolve-Path $Bin))
Write-Host "restore image: $Bin ($($image.Length) bytes) -> $APP_ADDR"

# Flash the image and report whether the board then boots it. Used twice: once
# to prove the restore works, once to actually restore.
#
# ⚠️ DO NOT reset the board when IAPTool exits. IAPTool is done once the last
# byte is sent -- the board is only then verifying the staged image, erasing the
# application region and copying it out of SDRAM, which takes seconds. Resetting
# there lands in the middle of the erase/write and leaves exactly the damage
# this case is otherwise trying to create deliberately. It happened, and it cost
# a confusing round of "the restore image does not boot".
#
# So: hold the log ports open across the whole flash and let the board reboot
# itself, which it does on success ("Checksum and signature OK. Rebooting...").
# The board's own words are the verdict; "File transfer complete" is IAPTool's
# account of its own sending, and says nothing about what the board decided.
function Invoke-FlashAndBoot([string]$what) {
    Section $what
    $out = Get-ScratchFile "s3_flash.out"
    $open = Open-LogPorts $Ports

    $p = Start-Process -FilePath $iap -ArgumentList @("ether", (Resolve-Path $Bin).Path, $Ip, "--downgrade=allow") `
        -NoNewWindow -PassThru -RedirectStandardOutput $out -RedirectStandardError "$out.err"

    $buf = @{}; foreach ($k in $open.Keys) { $buf[$k] = "" }
    while (-not $p.HasExited) {
        foreach ($k in @($open.Keys)) {
            try { if ($open[$k].BytesToRead -gt 0) { $buf[$k] += $open[$k].ReadExisting() } } catch {}
        }
        Start-Sleep -Milliseconds 50
    }
    # The board is still working here. Keep listening until it has written the
    # image and come back up.
    $tail = Read-LogPorts $open 15
    foreach ($k in $tail.Keys) { $buf[$k] += $tail[$k] }
    $serial = ($buf.Values -join "`n")
    ($serial -split "`r?`n" | Where-Object { $_.Trim() }) | ForEach-Object { Write-Host "    | $_" }

    $tool = ((Get-Content $out -ErrorAction SilentlyContinue) + (Get-Content "$out.err" -ErrorAction SilentlyContinue)) -join "`n"
    if ($tool -notmatch "File transfer complete") {
        Fail "IAPTool did not finish sending the image"
        ($tool -split "`n" | Select-Object -Last 8) | ForEach-Object { Write-Host "    $_" }
        return $false
    }
    if ($serial -notmatch "Checksum and signature OK") {
        Fail "the board did not accept the image (no 'Checksum and signature OK')"
        return $false
    }
    if ($serial -notmatch "\*\* APP Mod") {
        Fail "the board accepted the image but did not start the application"
        return $false
    }
    Ok "board accepted the image and booted it"
    return $true
}

# Reset and read the boot log. Returns $true when the application took over.
function Test-BoardBoots([string]$what) {
    Write-Host "  $what"
    $log = Get-BootLog
    if ($log -match "\*\* APP Mod") { Ok "  yes -- ** APP Mod ..."; return $true }
    Warn "  no -- the board stayed in the bootloader"
    return $false
}

# Reset over SWD with the log ports already open, so the boot banner is caught.
function Get-BootLog {
    $open = Open-LogPorts $Ports
    & $cli -c port=SWD mode=UR -rst 2>&1 | Out-Null
    $buf = Read-LogPorts $open 8
    $all = ($buf.Values -join "`n")
    ($all -split "`r?`n" | Where-Object { $_.Trim() }) | ForEach-Object { Write-Host "    | $_" }
    return $all
}

Assert-TargetReachable $cli

# --------------------------------------------------- 1. prove the restore ----

Section "1/4  prove the restore path BEFORE breaking anything"
if (-not (Invoke-FlashAndBoot "flashing the restore image")) {
    Fail "the restore image does not produce a bootable board -- refusing to break anything"
    exit 2
}
Ok "restore path proven; safe to proceed"

# ------------------------------------------------------ 2. break the app -----

Section "2/4  corrupting one byte of the installed application"

$offset = [int]($image.Length / 2)
$corrupt = $image.Clone()
$corrupt[$offset] = $corrupt[$offset] -bxor 0xFF
$corruptPath = Get-ScratchFile "s3_corrupt.bin"
[System.IO.File]::WriteAllBytes($corruptPath, $corrupt)
# Parenthesised: "..." -f a,b next to Write-Host would bind -f to
# -ForegroundColor and print nothing useful.
Write-Host ("  byte {0} of {1}: 0x{2:X2} -> 0x{3:X2}" -f $offset, $image.Length, $image[$offset], $corrupt[$offset])

# -w erases the sectors it needs before programming, which is what keeps this
# out of the double-programming ECC trap described at the top.
$wr = & $cli -c port=SWD mode=UR -w $corruptPath $APP_ADDR 2>&1
if ($wr -match "Error|error occured|cannot") {
    Fail "writing the corrupted image failed"
    $wr | ForEach-Object { Write-Host "    $_" }
    exit 2
}
Ok "corrupted image written"

# ------------------------------------------------------- 3. the verdict ------

Section "3/4  what does the board say now?"
$log = Get-BootLog

$metaPresent = $log -match "metadata present"
$sigInvalid  = $log -match "App signature invalid or absent"
$appBooted   = $log -match "\*\* APP Mod"

$why = @()
if ($appBooted)     { $why += "the board booted the application anyway -- the signature is NOT re-checked at boot (C3 is false)" }
if (-not $sigInvalid) { $why += "no 'App signature invalid or absent' line" }
if (-not $metaPresent) {
    $why += "'metadata present' missing -- the journal lost its metadata too, so this is not the pure boot-check case"
}

$verdict = 0
if ($why.Count -gt 0) {
    Fail "S3 FAILED:"
    $why | ForEach-Object { Write-Host "    $_" -ForegroundColor Red }
    $verdict = 1
} else {
    Ok "S3 PASSED -- metadata present, signature rejected, application not started"
    # Both strings come from the same branch of server_decide() (IAP_server.c),
    # so seeing "no valid application" here is expected, not a second failure.
    # What separates "app corrupted" from "metadata lost" is the metadata word
    # in the "Bootloader state:" line -- see docs/JOURNAL.md.
    Write-Host "  ('no valid application' in the UPLOAD banner is the same branch, not a separate fault)"
}

# ---------------------------------------------------------- 4. restore -------

if ($NoRestore) {
    Section "4/4  restore SKIPPED (-NoRestore)"
    Warn "the board will not boot an application until you flash a valid image"
    if ($verdict -ne 0) { exit 1 }
    exit 0
}

Section "4/4  restoring the board"
if (-not (Invoke-FlashAndBoot "flashing the restore image back")) {
    Fail "THE BOARD IS LEFT WITHOUT A BOOTABLE APPLICATION -- flash a valid image over ethernet or ST-Link"
    exit 1
}
Ok "board restored and booting normally"

Section "result"
if ($verdict -ne 0) { Fail "S3 FAILED (board was restored)"; exit 1 }
Ok "S3 passed and the board is back to normal"
exit 0
