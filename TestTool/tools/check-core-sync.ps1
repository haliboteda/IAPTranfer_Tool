# Compares the Arduino core the IDE actually loads ($CORE_LIVE) against the git
# repo ($CORE_REPO).
#
# The direction is one-way by design: edit and verify in $CORE_LIVE, then copy
# the verified result into $CORE_REPO and commit. $CORE_LIVE is not under
# version control, so anything verified there and not copied across exists on
# exactly one machine and dies with the next IDE reinstall.
#
# This is release-checklist item B3, automated.
#
# Exit 0 = identical, 1 = differences, 2 = a repo path is wrong.

. "$PSScriptRoot\_common.ps1"

foreach ($p in @($CORE_LIVE, $CORE_REPO)) {
    if (-not (Test-Path $p)) { Fail "not a directory: $p"; exit 2 }
}

# Three deliberate exclusions:
#   installed.json          the IDE's own install metadata, not source
#   tools/discovery/bin/    Go build output; the sources next to it are enough
#   *~                      editor backups
$skip = '^(installed\.json|tools\\discovery\\bin\\)|~$'

$onlyLive = @()
$diff     = @()
$onlyRepo = @()

$liveRoot = (Resolve-Path $CORE_LIVE).Path.TrimEnd('\')
$repoRoot = (Resolve-Path $CORE_REPO).Path.TrimEnd('\')

Section "core: live vs repo"
Write-Host "  live  $liveRoot"
Write-Host "  repo  $repoRoot"

$liveRel = @{}
Get-ChildItem $liveRoot -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($liveRoot.Length + 1)
    if ($rel -match $skip) { return }
    $liveRel[$rel] = $true
    $r = Join-Path $repoRoot $rel
    if (-not (Test-Path $r)) { $onlyLive += $rel; return }
    if ((Get-FileHash $_.FullName).Hash -ne (Get-FileHash $r).Hash) { $diff += $rel }
}

# The reverse direction matters too: a file deleted in live but still committed
# means the repo would reinstate dead code on the next package build.
Get-ChildItem $repoRoot -Recurse -File | ForEach-Object {
    $rel = $_.FullName.Substring($repoRoot.Length + 1)
    if ($rel -match $skip) { return }
    if ($rel -match '^\.git\\') { return }
    if (-not $liveRel.ContainsKey($rel)) { $onlyRepo += $rel }
}

foreach ($f in $onlyLive) { Fail   ("ONLY-LIVE  {0}" -f $f) }
foreach ($f in $diff)     { Fail   ("DIFF       {0}" -f $f) }
foreach ($f in $onlyRepo) { Warn   ("ONLY-REPO  {0}" -f $f) }

Section "result"
$n = $onlyLive.Count + $diff.Count
if ($n -gt 0) {
    Fail ("{0} file(s) verified in live but not in the repo -- copy them across and commit" -f $n)
    if ($onlyRepo.Count -gt 0) { Warn ("plus {0} file(s) present only in the repo" -f $onlyRepo.Count) }
    exit 1
}
if ($onlyRepo.Count -gt 0) {
    Warn ("live matches the repo, but {0} file(s) exist only in the repo" -f $onlyRepo.Count)
    exit 0
}
Ok "live and repo are identical"
exit 0
