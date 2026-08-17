# Put a hand-made owner record into the board's owner slot area, for testing
# the bootloader's record handling (requirement C10, module M1).
#
#   .\inject-owner-record.ps1                      one record, generation 1
#   .\inject-owner-record.ps1 -Generation 7        pick the generation
#   .\inject-owner-record.ps1 -Cleared             a factory-reset record
#   .\inject-owner-record.ps1 -Corrupt             wrong format_ver, must be ignored
#   .\inject-owner-record.ps1 -Restore             put the plain bootloader back
#
# ⚠️ WHY THIS IS NOT JUST "PROGRAMMER, WRITE 160 BYTES AT 0x0801E000"
#
# The owner area lives in the top 8K of the bootloader's OWN flash sector.
# STM32_Programmer_CLI erases a sector before writing into it, so a plain
#
#     STM32_Programmer_CLI -c port=SWD -w record.bin 0x0801E000
#
# erases the bootloader and leaves a board that prints nothing at all. That was
# established the hard way; the board needed a reflash to come back.
#
# So the record has to be flashed TOGETHER with the bootloader: this script
# pads the bootloader image out to the start of the owner area, appends the
# record, and writes the result as one image at 0x08000000. One erase, and both
# parts survive it.
#
# Once the bootloader can append records itself (M1 step 4) this stays useful
# for the cases that firmware is not supposed to be able to produce -- a record
# from a future format version, or one with a signature that does not verify.
#
# Exit 0 = flashed, 2 = prerequisites missing.

param(
    [uint32]$Generation = 1,
    [switch]$Cleared,
    [switch]$Corrupt,
    [switch]$Restore,
    [int]$Seconds = 10
)

. "$PSScriptRoot\_common.ps1"

$BIN = Join-Path $BOOT_REPO "Debug\open_plc_cube_ide.bin"
if (-not (Test-Path $BIN)) { Fail "no bootloader .bin at $BIN - build it first"; exit 2 }
$cli = Get-ProgrammerCli

# Must match owner_slot.h and the FLASH LENGTH in STM32H743IIKX_FLASH.ld.
$OWNER_BASE   = 0x0801E000
$OWNER_OFFSET = $OWNER_BASE - 0x08000000     # 0x1E000 = 122880
$RECORD_SIZE  = 160

$image = [System.IO.File]::ReadAllBytes($BIN)
if ($image.Length -gt $OWNER_OFFSET) {
    Fail ("the bootloader is {0:N0} B and would run into the owner area at {1:N0} B" -f $image.Length, $OWNER_OFFSET)
    exit 2
}

if ($Restore) {
    Section "restoring the plain bootloader"
    $out = $image
} else {
    Section "building bootloader + owner record"

    $rec = New-Object byte[] $RECORD_SIZE
    $rec[0] = 0x4F                                    # type 'O'
    $rec[1] = 5                                       # slots
    # format_ver: 1 normally, 99 for -Corrupt so the scanner must reject it
    $ver = if ($Corrupt) { 99 } else { 1 }
    [BitConverter]::GetBytes([uint16]$ver).CopyTo($rec, 2)
    [BitConverter]::GetBytes([uint32]$Generation).CopyTo($rec, 4)
    $flags = if ($Cleared) { 1 } else { 0 }
    [BitConverter]::GetBytes([uint32]$flags).CopyTo($rec, 8)

    # root_pubkey: all zero in a cleared record, otherwise a recognisable
    # pattern. It is not a real key -- nothing verifies it at this stage, and
    # a record that carried a real one would be no more convincing.
    if (-not $Cleared) {
        for ($i = 12; $i -lt 76; $i++) { $rec[$i] = 0xAA }
    }
    # prev_sig and reserved stay zero.

    Write-Host ("  type 'O', slots 5, format_ver {0}, generation {1}, flags {2}" -f $ver, $Generation, $flags)

    # 0xFF for the gap, so the unused part of the area still reads as erased.
    $out = New-Object byte[] ($OWNER_OFFSET + $RECORD_SIZE)
    for ($i = 0; $i -lt $out.Length; $i++) { $out[$i] = 0xFF }
    $image.CopyTo($out, 0)
    $rec.CopyTo($out, $OWNER_OFFSET)
}

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) "bootloader_with_owner.bin"
[System.IO.File]::WriteAllBytes($tmp, $out)
Write-Host ("  image: {0:N0} bytes" -f $out.Length)

Section "flashing"
Assert-TargetReachable $cli
$open = Open-LogPorts $LOG_PORTS
& $cli -c port=SWD mode=UR -w $tmp 0x08000000 -rst 2>&1 |
    Select-String -Pattern "Download|verified|Error|Reset" | ForEach-Object { Write-Host "  $_" }

$buf = Read-LogPorts $open $Seconds
$all = ($buf.Values -join "`n")

Section "boot log"
($all -split "`r?`n" | Where-Object { $_ -match "Owner slot|Bootloader state|APP Mod|UPLOAD Mod|NOT in effect|Reset cause" }) |
    ForEach-Object { Write-Host "    | $_" }

Section "result"
if ($all -notmatch "Owner slot:") {
    Fail "no 'Owner slot:' line - is this bootloader new enough?"
    exit 1
}
Ok "flashed; read the line above against what this record was meant to be"
exit 0
