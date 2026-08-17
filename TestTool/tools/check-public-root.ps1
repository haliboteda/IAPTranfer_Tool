# P6 -- the "this board trusts the published root" warning still knows which
# key the published root is.
#
#   .\check-public-root.ps1          check
#   .\check-public-root.ps1 -Print   also print a C initialiser to paste
#
# The bootloader warns on every boot when the root it verifies against is the
# one shipped with the project, whose private half is in the repository. It
# recognises that key by a SHA-256 fingerprint compiled into
# IAPServer/owner_slot.c.
#
# That fingerprint is a CONSTANT on purpose. Deriving it from fw_pubkey.inc at
# build time would make the comparison true for every build, so the warning
# would also fire on a customer board built with the customer's own key -- and
# a warning everyone learns to ignore protects nobody. See docs/OWNERSHIP.md.
#
# The cost of it being a constant is that rotating the project's default key
# (IAPServer/keys/rotate_keys.sh) leaves it pointing at the OLD key. Factory
# boards would then trust a published key and say nothing, which is the exact
# failure this warning exists to prevent. Nothing else would notice: the
# firmware builds, boots, and looks healthy.
#
# So this compares the two and fails when they drift.
#
# ⚠️ In a customer's fork the two are SUPPOSED to differ -- that is what having
# their own root means. This check belongs to this repository, where the
# default key is by definition the published one.
#
# Exit 0 = they agree, 1 = drifted, 2 = could not read one of the files.

param([switch]$Print)

. "$PSScriptRoot\_common.ps1"

$inc = Join-Path $BOOT_REPO "IAPServer\keys\fw_pubkey.inc"
$src = Join-Path $BOOT_REPO "IAPServer\owner_slot.c"
foreach ($f in @($inc, $src)) {
    if (-not (Test-Path $f)) { Fail "not found: $f"; exit 2 }
}

Section "published root fingerprint"

# The key as the firmware sees it: 64 raw bytes, X||Y.
$hex = ([regex]::Matches((Get-Content $inc -Raw), '0x([0-9a-fA-F]{2})') |
        ForEach-Object { $_.Groups[1].Value }) -join ""
if ($hex.Length -ne 128) {
    Fail "fw_pubkey.inc parsed to $($hex.Length) hex chars, expected 128"
    exit 2
}
$bytes = [byte[]]@(for ($i = 0; $i -lt $hex.Length; $i += 2) {
    [Convert]::ToByte($hex.Substring($i, 2), 16)
})
$want = ([System.Security.Cryptography.SHA256]::Create().ComputeHash($bytes) |
         ForEach-Object { $_.ToString('x2') }) -join ""

# The constant the firmware compares against.
$text = Get-Content $src -Raw
$m = [regex]::Match($text,
    'k_published_root_sha256\s*\[\s*32\s*\]\s*=\s*\{(?<body>[^}]*)\}')
if (-not $m.Success) {
    Fail "could not find k_published_root_sha256[32] in owner_slot.c"
    exit 2
}
$have = ([regex]::Matches($m.Groups['body'].Value, '0x([0-9a-fA-F]{2})') |
         ForEach-Object { $_.Groups[1].Value }) -join ""

Write-Host "  key      $($hex.Substring(0,32))..."
Write-Host "  computed $want"
Write-Host "  compiled $have"

if ($Print) {
    Section "C initialiser"
    $line = ""
    for ($i = 0; $i -lt 32; $i++) {
        $line += ("0x{0}, " -f $want.Substring($i * 2, 2))
        if ($i % 8 -eq 7) { Write-Host ("`t" + $line.TrimEnd()); $line = "" }
    }
}

# --- and does the built image actually contain that key? ---------------------
#
# The check above only compares two source files. It cannot see a build that
# still holds an older key, and that is a real way to be wrong: swapping
# fw_pubkey.inc for a file with an OLDER timestamp (any plain copy or restore
# preserves the source's timestamp) leaves make thinking fw_pubkey.o is current.
# The firmware then builds and boots perfectly while trusting the previous root.
#
# It happened during development, and the only reason it was noticed is that the
# board stopped verifying an application signed with the intended key. On a
# rotation where both keys are ours, nothing would have looked wrong at all.
$staleBuild = $false
$bin = Join-Path $BOOT_REPO "Debug\open_plc_cube_ide.bin"
if (Test-Path $bin) {
    Section "built image"
    $img = [System.IO.File]::ReadAllBytes($bin)
    $found = -1
    for ($i = 0; $i -le $img.Length - $bytes.Length; $i++) {
        if ($img[$i] -ne $bytes[0]) { continue }
        $match = $true
        for ($j = 1; $j -lt $bytes.Length; $j++) {
            if ($img[$i + $j] -ne $bytes[$j]) { $match = $false; break }
        }
        if ($match) { $found = $i; break }
    }
    if ($found -ge 0) {
        Ok ("  the key from fw_pubkey.inc is in Debug/*.bin at 0x{0:X}" -f $found)
    } else {
        Warn "  Debug/*.bin does NOT contain the key from fw_pubkey.inc"
        Warn "  -- the build is stale. Touch fw_pubkey.inc and rebuild."
        $staleBuild = $true
    }
} else {
    Write-Host "  (no Debug/*.bin to check; build the bootloader to include this)"
}

Section "result"
if ($have.Length -ne 64) {
    Fail "the compiled constant parsed to $($have.Length) hex chars, expected 64"
    exit 2
}
if ($have -ne $want) {
    Fail "DRIFT: owner_slot.c does not recognise the key currently in fw_pubkey.inc."
    Fail "Factory boards would trust a published root and stay silent about it."
    Fail "Regenerate the constant (-Print gives it) and rebuild the bootloader."
    exit 1
}
if ($staleBuild) {
    Fail "the fingerprint is right, but the built image carries a different key"
    exit 1
}
Ok "the warning recognises the published root, and the build carries it"
exit 0
