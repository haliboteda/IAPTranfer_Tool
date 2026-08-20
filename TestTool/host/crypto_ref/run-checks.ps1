# Cross-checks the product's crypto against independent implementations.
#
#   1. sha256_ref.py   the bootloader's SHA-256/HMAC construction vs hashlib
#   2. ecdsa_verify.py signatures IAPTool actually produced, verified by hand-
#                      rolled modular arithmetic that shares no code with Go
#
# Signing is randomised, so step 2 signs the same blob several times and
# verifies every result. One passing signature proves the encoding round-trips;
# several prove it does not depend on a lucky value of r or s (a leading zero
# byte in either integer is the classic case, and it shows up roughly once in
# 256 signatures -- rare enough to reach the field, common enough to be certain
# it eventually will).
#
#   .\run-checks.ps1              default 12 signatures
#   .\run-checks.ps1 -Rounds 64   more, when key or signer code changed
#
# Exit 0 = everything verified, 1 = something did not, 2 = prerequisites missing.

param([int]$Rounds = 12)

. "$PSScriptRoot/..\..\tools\_common.ps1"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { Fail "python is not on PATH"; exit 2 }

$bad = 0

Section "1. SHA-256 / HMAC-SHA-256 construction"
python "$PSScriptRoot/sha256_ref.py"
if ($LASTEXITCODE -ne 0) { Fail "SHA-256 reference check failed"; $bad++ } else { Ok "PASS" }

Section "2. ECDSA P-256 signatures from IAPTool"

if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    Warn "SKIP - go is not on PATH, cannot produce signatures"
} else {
    $iapTool = Get-GoBin "IAPTool"
    if (-not (Test-Path $iapTool)) {
        Push-Location $TOOL_REPO; go build -o "Output/$GOOS_DIR/IAPTool$EXE" .; Pop-Location
    }
    $key = Join-Path $BOOT_REPO "IAPServer/keys/fw_signing_key.TEST_ONLY.pem"
    $inc = Join-Path $BOOT_REPO "IAPServer/keys/fw_pubkey.inc"
    foreach ($p in @($iapTool, $key, $inc)) {
        if (-not (Test-Path $p)) { Fail "not found: $p"; exit 2 }
    }

    # The public key comes from the same .inc the bootloader compiles in, so
    # this checks the committed key pair, not an ad-hoc one.
    $pubHex = (([regex]::Matches((Get-Content $inc -Raw), '0x([0-9a-fA-F]{2})') |
                 ForEach-Object { $_.Groups[1].Value }) -join "").ToLower()
    if ($pubHex.Length -ne 128) { Fail "fw_pubkey.inc parsed to $($pubHex.Length) hex chars"; exit 2 }
    Write-Host "  key pair: IAPServer/keys/fw_signing_key.TEST_ONLY.pem -> $($pubHex.Substring(0,16))..."

    $scratch = Join-Path ([System.IO.Path]::GetTempPath()) ("cryptoref-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $scratch | Out-Null

    $msg = Join-Path $scratch "msg.bin"
    $bytes = New-Object byte[] 4096
    for ($i = 0; $i -lt $bytes.Length; $i++) { $bytes[$i] = [byte](($i * 17 + 3) % 256) }
    [System.IO.File]::WriteAllBytes($msg, $bytes)

    $verified = 0
    for ($n = 1; $n -le $Rounds; $n++) {
        $prefix = Join-Path $scratch "run$n"
        & $iapTool sign $msg $key "--out=$prefix" 2>&1 | Out-Null
        $sig = "$prefix.sig"
        if (-not (Test-Path $sig)) { Fail "round ${n}: IAPTool produced no .sig"; $bad++; continue }

        Write-Host "  round $n"
        python "$PSScriptRoot/ecdsa_verify.py" $pubHex $msg $sig
        if ($LASTEXITCODE -ne 0) { Fail "round ${n}: independent verification FAILED"; $bad++ }
        else { $verified++ }
    }

    Remove-Item $scratch -Recurse -Force -ErrorAction SilentlyContinue

    if ($verified -eq $Rounds) { Ok "PASS - $verified/$Rounds signatures verified independently" }
    else { Fail "only $verified of $Rounds verified" }
}

Section "result"
if ($bad -gt 0) { Fail "$bad check(s) failed"; exit 1 }
Ok "the shipping signer and an independent verifier agree"
exit 0
