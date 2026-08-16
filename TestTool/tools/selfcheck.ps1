# Every check that needs no board, in one command.
#
# This is layer 1 of the acceptance checklist (acceptance/checklist.md section
# A). It is cheap enough to run after every edit, and each round of on-board
# debugging costs an order of magnitude more -- so nothing here should ever be
# skipped on the way to the board.
#
#   .\tools\selfcheck.ps1              run everything
#   .\tools\selfcheck.ps1 -Quick       skip the slow ones (fakeboard, C unit tests)
#
# Exit 0 = all pass, 1 = at least one failed. A check whose prerequisite is
# absent (no gcc, no python) reports SKIP and does not fail the run -- but the
# summary always names it, because a silently skipped check reads as a pass.

param([switch]$Quick)

. "$PSScriptRoot\_common.ps1"

$results = New-Object System.Collections.ArrayList

function Step {
    param([string]$Id, [string]$Name, [scriptblock]$Body, [string]$Needs)

    Section "$Id  $Name"

    if ($Needs -and -not (Get-Command $Needs -ErrorAction SilentlyContinue)) {
        Warn "SKIP - $Needs is not on PATH"
        [void]$results.Add([pscustomobject]@{ Id = $Id; Name = $Name; State = "SKIP"; Note = "$Needs missing" })
        return
    }

    $out = & $Body
    $code = $LASTEXITCODE
    if ($out) { $out | ForEach-Object { Write-Host "  $_" } }

    if ($code -eq 0) {
        Ok "PASS"
        [void]$results.Add([pscustomobject]@{ Id = $Id; Name = $Name; State = "PASS"; Note = "" })
    } else {
        Fail "FAIL (exit $code)"
        [void]$results.Add([pscustomobject]@{ Id = $Id; Name = $Name; State = "FAIL"; Note = "exit $code" })
    }
}

Push-Location $TOOL_REPO

Step "A1" "host Go tests (crypto primitives, key derivation, challenge/response)" {
    go test ./TestTool/... 2>&1
} "go"

Step "A3" "go vet over the whole module" {
    go vet ./... 2>&1
} "go"

Step "A7" "firmware version agrees in all three places" {
    & "$PSScriptRoot\check-version-sync.ps1" 2>&1
}

Step "A8" "cross-repo mirrored code has not diverged" {
    & "$PSScriptRoot\check-mirror-sync.ps1" 2>&1
}

Step "A9" "Arduino core: live matches the git repo" {
    & "$PSScriptRoot\check-core-sync.ps1" 2>&1
}

if (-not $Quick) {
    Step "A2" "host C unit tests (real sha256.c / iap_keyderive.c / iap_auth.c)" {
        & "$PSScriptRoot\..\host\bootloader_unit\build.ps1" 2>&1
    } "gcc"

    Step "A10" "IAPTool key-match logic against a stand-in board" {
        & "$PSScriptRoot\..\host\fakeboard\run-cases.ps1" 2>&1
    } "python"

    Step "A11" "crypto cross-check against independent implementations" {
        & "$PSScriptRoot\..\host\crypto_ref\run-checks.ps1" -Rounds 8 2>&1
    } "python"
}

Pop-Location

Section "summary"
$w = ($results | ForEach-Object { $_.Name.Length } | Measure-Object -Maximum).Maximum
foreach ($r in $results) {
    $line = "{0,-4} {1,-$w}  {2}" -f $r.Id, $r.Name, $r.State
    if ($r.Note) { $line += " ($($r.Note))" }
    switch ($r.State) {
        "PASS" { Ok   $line }
        "SKIP" { Warn $line }
        "FAIL" { Fail $line }
    }
}

$failed  = @($results | Where-Object { $_.State -eq "FAIL" }).Count
$skipped = @($results | Where-Object { $_.State -eq "SKIP" }).Count

Write-Host ""
if ($failed -gt 0) {
    Fail "$failed failed - do not go to the board until these are green"
    exit 1
}
if ($skipped -gt 0) {
    Warn "$skipped skipped - those areas are unverified on this machine"
}
Ok "host-side checks pass; next is acceptance/checklist.md A4 (build) and A5 (flash)"
exit 0
