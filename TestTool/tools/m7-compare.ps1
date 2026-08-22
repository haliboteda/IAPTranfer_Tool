# M7 acceptance harness: run a PowerShell check and its Python translation and
# compare the two outputs byte for byte.
#
#     .\tools\m7-compare.ps1                    compare every known pair
#     .\tools\m7-compare.ps1 -Only version      compare one pair
#     .\tools\m7-compare.ps1 -List              just list the pairs
#     .\tools\m7-compare.ps1 -Keep              leave the captured output on disk
#
# Why this exists: the danger in M7 is not "the Python version does not run", it
# is "the Python version runs, passes, and tests something else". Comparing
# verdicts is not enough for that -- a check that silently stopped looking at a
# file still says PASS. So the acceptance criterion is the whole output, not the
# exit code.
#
# Both scripts are run as child processes so Write-Host and print() alike land
# on a real stdout that can be captured.
#
# Step 2's four static checks live in tools/; step 3's six host suites live in
# host/*/, which is why each pair carries its own directory. The host suites are
# minutes rather than seconds, so -Only is the normal way to work on one.
#
# Exit 0 = every pair matched, 1 = at least one differed.

param(
    [string]$Only = "",
    [switch]$List,
    [switch]$Keep
)

. "$PSScriptRoot/_common.ps1"

$TestTool = Split-Path -Parent $PSScriptRoot

# Known is the escape hatch, and it is deliberately narrow: an exact string that
# is ALLOWED to differ, with the reason. Anything not listed here is a failure.
#
# The point of listing them rather than loosening the comparison is that each
# one had to be argued for once, in writing, and shows up in the output every
# time it fires. A fuzzy comparison would hide the next one.
#
# Volatile is the second escape hatch and is narrower still: a regex whose match
# is replaced on BOTH sides, for output that cannot be equal because it is not
# the same run -- a fresh temp directory name. Use it only where the text
# carries no verdict. If you find yourself wanting it for anything a reader
# would check, the answer is to stop printing that thing, not to mask it.
$pairs = @(
    @{ Name = "version";     Dir = "tools"; Ps1 = "check-version-sync.ps1"; Py = "check_version_sync.py" },
    @{ Name = "mirror";      Dir = "tools"; Ps1 = "check-mirror-sync.ps1";  Py = "check_mirror_sync.py"  },
    @{ Name = "core";        Dir = "tools"; Ps1 = "check-core-sync.ps1";    Py = "check_core_sync.py"    },
    @{ Name = "public-root"; Dir = "tools"; Ps1 = "check-public-root.ps1";  Py = "check_public_root.py";
       Known = @(
           @{ From = "(-Print gives it)"; To = "(--print gives it)";
              Why  = "the switch really is named differently; printing -Print from the Python version would send the reader after a switch it does not have" }
       )
    },

    # ---- step 3: the host suites -------------------------------------------
    @{ Name = "hostunit";  Dir = "host/bootloader_unit"; Ps1 = "build.ps1"; Py = "build.py" },
    @{ Name = "variant";   Dir = "host/variant_check";   Ps1 = "build.ps1"; Py = "build.py" },
    @{ Name = "cryptoref"; Dir = "host/crypto_ref"; Ps1 = "run-checks.ps1"; Py = "run_checks.py" },
    @{ Name = "fakeboard"; Dir = "host/fakeboard"; Ps1 = "run-cases.ps1"; Py = "run_cases.py";
       Volatile = @(
           @{ Pattern = '(?m)^(scratch|kept): .*$'; Replace = '$1: <scratch>';
              Why = "a fresh random temp directory per run, printed for a human who wants to look inside; no verdict depends on it" }
       )
    },
    @{ Name = "downgrade"; Dir = "host/fakeboard"; Ps1 = "run-downgrade.ps1"; Py = "run_downgrade.py";
       Volatile = @(
           @{ Pattern = '(?m)^(scratch|kept): .*$'; Replace = '$1: <scratch>';
              Why = "same as the fakeboard pair" }
       )
    },
    # A subset on purpose. The full P5 run is about ten minutes, so comparing it
    # twice would cost twenty for no extra coverage of the SCRIPT: one library's
    # examples already exercise every branch except "there are more of them".
    # The full run stays a thing you do by hand, as it always was.
    @{ Name = "examples"; Dir = "host/examples_build"; Ps1 = "build.ps1"; Py = "build.py";
       Ps1Args = @("-Only", "SDRAM"); PyArgs = @("--only", "SDRAM");
       Note = "one library only (-Only/--only SDRAM); the full ten-minute run is not compared" },

    # ---- step 4: the aggregator --------------------------------------------
    # Compare = "summary" narrows the comparison to the verdict table, and it is
    # the one pair where whole-output equality is not achievable. PowerShell has
    # two output channels: Write-Host reaches the console as it happens, while a
    # native command's stdout goes into the pipeline and Step prints it -- indented
    # -- only once the step has ended. So in selfcheck.ps1's X1-X2 the reference
    # implementations' output lands AFTER that step's own "result" banner.
    # Imitating that would mean making run_checks.py withhold its children's
    # output, breaking a pair that is already byte-identical. M7's own criterion
    # for step 4 is "the same verdict for each of the 12 items", which is exactly
    # what the summary table is.
    #
    # -Quick/--quick on purpose: the five slow steps are compared individually as
    # their own pairs above, so running them again here would add twelve minutes
    # and no coverage. What this pair is for is the orchestration and the table.
    @{ Name = "selfcheck"; Dir = "tools"; Ps1 = "selfcheck.ps1"; Py = "selfcheck.py";
       Ps1Args = @("-Quick"); PyArgs = @("--quick"); Compare = "summary";
       Note = "-Quick/--quick, and only the summary table is compared -- see the comment above" }
)

if ($List) {
    Section "pairs"
    foreach ($p in $pairs) {
        Write-Host ("  {0,-12} {1}/{2}  vs  {3}" -f $p.Name, $p.Dir, $p.Ps1, $p.Py)
    }
    exit 0
}

if ($Only) { $pairs = @($pairs | Where-Object { $_.Name -eq $Only }) }
if ($pairs.Count -eq 0) {
    Fail "no pair named '$Only'"
    Warn "  run with -List to see them"
    exit 2
}

# ENV is not a script pair -- it is common.py --probe against selfcheck's own
# banner -- so it is compared by hand, not here. Saying so beats a silent gap.
$python = if ($PLATFORM -eq "windows") { "python" } else { "python3" }

$bad = 0
foreach ($p in $pairs) {
    Section ("M7 compare: {0}" -f $p.Name)
    if ($p.Note) { Write-Host ("  note: {0}" -f $p.Note) }

    $dir = Join-Path $TestTool $p.Dir
    $ps1 = Join-Path $dir $p.Ps1
    $py  = Join-Path $dir $p.Py
    $missing = @($ps1, $py) | Where-Object { -not (Test-Path $_) }
    if ($missing.Count -gt 0) {
        $missing | ForEach-Object { Fail "missing: $_" }
        $bad++
        continue
    }

    # @(...) around the whole thing, not just the else branch. A single-element
    # array coming out of an `if` block is unrolled to its element, so
    # @("--quick") arrives here as the STRING "--quick" -- and splatting a string
    # splats its characters. The symptom was
    #     selfcheck.py: error: unrecognized arguments: - - q u i c k
    # The pairs above with two arguments were unaffected, which is why this only
    # showed up when the first one-argument pair was added.
    $ps1Args = @(if ($p.Ps1Args) { $p.Ps1Args } else { @() })
    $pyArgs  = @(if ($p.PyArgs)  { $p.PyArgs }  else { @() })

    # NO_COLOR keeps the Python side from emitting escapes if it ever runs on a
    # tty; PowerShell child processes never colour a redirected stream.
    $env:NO_COLOR = "1"
    $aRaw = & powershell -NoProfile -ExecutionPolicy Bypass -File $ps1 @ps1Args 2>&1
    $aCode = $LASTEXITCODE
    $bRaw = & $python $py @pyArgs 2>&1
    $bCode = $LASTEXITCODE
    Remove-Item Env:\NO_COLOR -ErrorAction SilentlyContinue

    $a = ($aRaw | Out-String) -replace "`r`n", "`n"
    $b = ($bRaw | Out-String) -replace "`r`n", "`n"

    # Rewrite the PowerShell side's known-different strings into the Python
    # form, and say so. A deviation that stops occurring is worth noticing too,
    # so an entry that never fires is reported as stale.
    foreach ($k in @($p.Known)) {
        if (-not $k) { continue }
        if ($a.Contains($k.From)) {
            $a = $a.Replace($k.From, $k.To)
            Warn ("  known deviation, allowed: '{0}' -> '{1}'" -f $k.From, $k.To)
            Warn ("    {0}" -f $k.Why)
        } elseif ($aCode -ne 0) {
            # Only meaningful once the failure path has actually run. Reporting
            # it on every clean run would make the entry permanent noise, and a
            # warning that is always there is one nobody reads.
            Warn ("  known deviation never appeared in this run: '{0}'" -f $k.From)
            Warn ("    if the line is gone for good, delete the entry")
        }
    }

    # Volatile rules are applied to both sides, and are reported whenever they
    # fire so a reader always knows what was masked before the comparison.
    foreach ($v in @($p.Volatile)) {
        if (-not $v) { continue }
        if ($a -match $v.Pattern -or $b -match $v.Pattern) {
            $a = $a -replace $v.Pattern, $v.Replace
            $b = $b -replace $v.Pattern, $v.Replace
            Warn ("  masked on both sides: /{0}/" -f $v.Pattern)
            Warn ("    {0}" -f $v.Why)
        } else {
            Warn ("  volatile rule never matched: /{0}/" -f $v.Pattern)
            Warn ("    if that output is gone for good, delete the entry")
        }
    }

    # Written before any narrowing, so a failure always leaves the FULL captured
    # output on disk -- the narrowed text is what gets compared, not what gets
    # investigated.
    $aFile = Get-ScratchFile ("m7-{0}.ps1.out" -f $p.Name)
    $bFile = Get-ScratchFile ("m7-{0}.py.out"  -f $p.Name)
    [System.IO.File]::WriteAllText($aFile, $a)
    [System.IO.File]::WriteAllText($bFile, $b)

    # A narrowed comparison keeps only the verdict table. It is announced every
    # time, because "identical" has to mean the same thing to every reader.
    if ($p.Compare -eq "summary") {
        $marker = "===== summary"
        $ai = $a.IndexOf($marker)
        $bi = $b.IndexOf($marker)
        if ($ai -lt 0 -or $bi -lt 0) {
            $where = if ($ai -lt 0 -and $bi -lt 0) { "both sides" }
                     elseif ($ai -lt 0) { "the ps1 side" } else { "the py side" }
            Fail ("narrowed to the summary table, but '{0}' is missing on {1}" -f $marker, $where)
            Fail ("  ps1 captured {0} char(s), exit {1}: {2}" -f $a.Length, $aCode, $aFile)
            Fail ("  py  captured {0} char(s), exit {1}: {2}" -f $b.Length, $bCode, $bFile)
            $bad++
            continue
        }
        Warn ("  narrowed: comparing only from '{0}' onwards" -f $marker)
        Warn  "    whole-output equality is not achievable for this pair; the verdict table is the criterion"
        $a = $a.Substring($ai)
        $b = $b.Substring($bi)
    }

    if ($aCode -ne $bCode) {
        Fail ("exit code differs: ps1={0} py={1}" -f $aCode, $bCode)
        $bad++
    }

    if ($a -eq $b) {
        Ok ("identical, {0} line(s), exit {1}" -f ($a -split "`n").Count, $aCode)
        if (-not $Keep) { Remove-Item $aFile, $bFile -ErrorAction SilentlyContinue }
        continue
    }

    $bad++
    Fail "output differs"
    $al = $a -split "`n"
    $bl = $b -split "`n"
    $n = [Math]::Max($al.Count, $bl.Count)
    $shown = 0
    for ($i = 0; $i -lt $n; $i++) {
        $x = if ($i -lt $al.Count) { $al[$i] } else { "<missing>" }
        $y = if ($i -lt $bl.Count) { $bl[$i] } else { "<missing>" }
        if ($x -ceq $y) { continue }
        if ($shown -ge 20) { Warn "  ... more differences not shown"; break }
        Warn ("  line {0}" -f ($i + 1))
        Warn ("    ps1: {0}" -f $x)
        Warn ("    py : {0}" -f $y)
        $shown++
    }
    Write-Host ("  full output kept at:")
    Write-Host ("    {0}" -f $aFile)
    Write-Host ("    {0}" -f $bFile)
}

Section "result"
if ($bad -gt 0) {
    Fail ("{0} pair(s) did not match -- the Python version is NOT a drop-in yet" -f $bad)
    exit 1
}
Ok ("all {0} pair(s) produce identical output" -f $pairs.Count)
exit 0
