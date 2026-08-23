"""Every check that needs no board, in one command.

This is layer 1 of the acceptance checklist (acceptance/checklist.md section A).
It is cheap enough to run after every edit, and each round of on-board debugging
costs an order of magnitude more -- so nothing here should ever be skipped on the
way to the board.

    python tools/selfcheck.py              run everything
    python tools/selfcheck.py --quick      skip the slow ones (fakeboard, C unit tests)
    python tools/selfcheck.py --list       say what each step is, run nothing

Exit 0 = all pass, 1 = at least one failed. A check whose prerequisite is absent
(no gcc, no arduino-cli) reports SKIP and does not fail the run -- but the summary
always names it, because a silently skipped check reads as a pass.

Step ids ARE the case ids (2026-08-22). They used to be A0..A14, a numbering of
their own -- and every one of those numbers was only ever an alias for a case that
already had an id: A12 was DG1, A13 was P4, A2 was H2. Worse, A1/A2/A3/A7 collided
with requirement ids of the same name, so "A7 passed" had four possible meanings.
Dropping the alias deletes a whole namespace and one of those collisions. The map
from the old numbers is in open_plc_cube_ide/docs/ID-MAP.md.

Each step announces what requirement it covers, because "A12 passed" told you
nothing about what is now known to work.

M7 step 4. The acceptance criterion for this one is NOT byte-identical output, it
is "the same verdict for each of the 12 items" -- which is what M7 wrote, and it
is the only criterion that can be met. Why:

  PowerShell has two output channels. Write-Host goes straight to the console as
  it happens; a native command's stdout goes into the pipeline, which Step
  collects into $out and prints -- indented two spaces -- only after the whole
  step has finished. So in selfcheck.ps1's A11, the output of sha256_ref.py and
  ecdsa_verify.py appears AFTER the step's own "===== result" banner, which reads
  as though the verification ran after the conclusion.

  Reproducing that ordering here would mean making run_checks.py withhold its
  children's output, and run_checks.py is already proven byte-identical to
  run-checks.ps1. Breaking a passing pair to imitate an artefact is the wrong
  trade, so this version prints in the order things happen.

Two more deliberate differences, both of which make a machine MORE usable and
neither of which can change a verdict on a machine that has the tools:

  * the platform line names Python instead of PowerShell (the M7 step 1
    deviation, already recorded);
  * K1-K6 / X1-X2 / DG1 do not gate on finding "python" on PATH. This interpreter
    is what runs them, so there is nothing to look up -- and the PowerShell
    version's literal "python" is what makes those three SKIP on a python3-only
    machine.
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (Fail, Ok, Section, Warn, cfg, have_cmd,  # noqa: E402
                    probe, python_exe)

TESTTOOL = HERE.parent
results = []

# What each step is, in run order. This is the ONE place the step list lives:
# --list prints it, and run_step() looks up "covers" here and raises if a step is
# missing, so the two can never drift apart.
#
# "covers" is the requirement id in open_plc_cube_ide/docs/STATUS.md that this
# case is the evidence for. A case that covers nothing should not exist.
CATALOG = [
    ("ENV",     "-",           "this machine has the toolchain"),
    ("H1",      "C5",          "host Go tests (crypto primitives, key derivation, challenge/response)"),
    ("H3",      "-",           "go vet over the whole module"),
    ("P1",      "D7",          "firmware version agrees in all three places"),
    ("P2",      "D8 A6 A7 C7 E1 E6", "cross-repo mirrored code has not diverged"),
    ("P3",      "D9",          "Arduino core: live matches the git repo"),
    ("P6",      "C10",         "the published-root warning still recognises the published root"),
    ("P7",      "-",           "STATUS.md and TEST-CASES.md name the same set of cases"),
    ("P8",      "-",           "no claim is written out in more than one document"),
    ("P9",      "-",           "every path a document names actually exists"),
    ("H2",      "C5",          "host C unit tests (real sha256.c / iap_keyderive.c / iap_auth.c)"),
    ("K1-K6",   "C8",          "IAPTool key-match logic against a stand-in board"),
    ("X1-X2",   "C9",          "crypto cross-check against independent implementations"),
    ("DG1",     "C6",          "downgrade guard: older image refused, and not uploaded"),
    ("P4",      "E6",          "Arduino variant assertions (the FMC reserved-pin table)"),
]
COVERS = {cid: covers for cid, covers, _ in CATALOG}

STATUS_DOC = "open_plc_cube_ide/docs/STATUS.md"
CRITERIA_DOC = "TestTool/TEST-CASES.md"


def print_catalog():
    """--list: what would run, and what each step is evidence for."""
    Section("selfcheck steps")
    print("  step ids are case ids; 'covers' points into %s" % STATUS_DOC)
    print("  pass/fail criteria for each case: %s" % CRITERIA_DOC)
    print("")
    width = max(len(c) for c, _, _ in CATALOG)
    cov = max(len(v) for _, v, _ in CATALOG)
    for cid, covers, name in CATALOG:
        print("  %-*s  covers %-*s  %s" % (width, cid, cov, covers, name))
    print("")
    print("  %d steps. --quick skips H2 / K1-K6 / X1-X2 / DG1 / P4." % len(CATALOG))
    print("  P7, P8 and P9 check the documents, not the firmware.")


def record(step_id, name, state, note=""):
    results.append({"id": step_id, "name": name, "state": state, "note": note})


def run_step(step_id, name, argv, needs=None, cwd=None, indent=0):
    """One step: announce what it is, run it, record PASS/SKIP/FAIL.

    needs is a command name or an absolute path -- a tool installed for one check
    only (HOST_CC) has no business being on PATH.

    indent shifts the child's output, matching what selfcheck.ps1 does to a
    native command's piped stdout. The Python suites format their own output and
    are left alone, exactly as their PowerShell twins are.
    """
    if step_id not in COVERS:
        raise KeyError("step %r is not in CATALOG -- add it there too" % step_id)

    Section("%s  %s" % (step_id, name))
    # Say what this proves before running it. "A12 passed" told nobody anything.
    print("  covers %s   (%s)" % (COVERS[step_id], STATUS_DOC))

    if needs and not have_cmd(str(needs)) and not Path(str(needs)).exists():
        Warn("SKIP - %s not found" % needs)
        record(step_id, name, "SKIP", "%s missing" % needs)
        return

    sys.stdout.flush()
    if indent:
        proc = subprocess.run([str(a) for a in argv],
                              cwd=None if cwd is None else str(cwd),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, errors="replace")
        for line in (proc.stdout or "").splitlines():
            print("%s%s" % (" " * indent, line))
        code = proc.returncode
    else:
        proc = subprocess.run([str(a) for a in argv],
                              cwd=None if cwd is None else str(cwd))
        code = proc.returncode

    if code == 0:
        Ok("PASS")
        record(step_id, name, "PASS")
    else:
        Fail("FAIL (exit %d)" % code)
        record(step_id, name, "FAIL", "exit %d" % code)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--quick", action="store_true",
                    help="skip the slow ones (fakeboard, C unit tests)")
    ap.add_argument("--list", action="store_true", dest="list_only",
                    help="say what each step is and what it covers, run nothing")
    args = ap.parse_args()

    if args.list_only:
        print_catalog()
        return 0

    # ------------------------------------------------------------------ ENV
    # What this machine actually has. Runs first because every failure below is
    # easier to read once you know whether the thing was even installed -- and
    # because on a freshly cloned machine this is the check that says what to go
    # and install. Nothing here fails the run: CubeIDE and a serial port are
    # needed to reach the board, not to pass the host-side checks.
    missing = probe()
    if missing:
        record("ENV", "this machine has the toolchain", "SKIP", ", ".join(missing))
    else:
        record("ENV", "this machine has the toolchain", "PASS")

    tool_repo = cfg.TOOL_REPO

    run_step("H1", "host Go tests (crypto primitives, key derivation, challenge/response)",
             ["go", "test", "./TestTool/..."], needs="go", cwd=tool_repo, indent=2)

    run_step("H3", "go vet over the whole module",
             ["go", "vet", "./..."], needs="go", cwd=tool_repo, indent=2)

    run_step("P1", "firmware version agrees in all three places",
             [python_exe(), HERE / "check_version_sync.py"], cwd=tool_repo)

    run_step("P2", "cross-repo mirrored code has not diverged",
             [python_exe(), HERE / "check_mirror_sync.py"], cwd=tool_repo)

    run_step("P3", "Arduino core: live matches the git repo",
             [python_exe(), HERE / "check_core_sync.py"], cwd=tool_repo)

    run_step("P6", "the published-root warning still recognises the published root",
             [python_exe(), HERE / "check_public_root.py"], cwd=tool_repo)

    # These two guard the documents rather than the product. They are here because
    # a table that has drifted from the cases, or a fact claimed in two files, is
    # exactly as expensive to find later as a code divergence -- and 2026-08-22
    # proved nobody finds either by eye.
    run_step("P7", "STATUS.md and TEST-CASES.md name the same set of cases",
             [python_exe(), HERE / "check_status_sync.py"], cwd=tool_repo)

    run_step("P8", "no claim is written out in more than one document",
             [python_exe(), HERE / "check_doc_dupes.py"], cwd=tool_repo)

    run_step("P9", "every path a document names actually exists",
             [python_exe(), HERE / "check_doc_paths.py"], cwd=tool_repo)

    if not args.quick:
        # HOST_CC from config wins; otherwise fall back to whatever "gcc"
        # resolves to on PATH, so a machine with neither still reports SKIP by
        # name.
        cc_need = getattr(cfg, "HOST_CC", "") or "gcc"

        run_step("H2", "host C unit tests (real sha256.c / iap_keyderive.c / iap_auth.c)",
                 [python_exe(), TESTTOOL / "host" / "bootloader_unit" / "build.py"],
                 needs=cc_need, cwd=tool_repo)

        run_step("K1-K6", "IAPTool key-match logic against a stand-in board",
                 [python_exe(), TESTTOOL / "host" / "fakeboard" / "run_cases.py"],
                 cwd=tool_repo)

        run_step("X1-X2", "crypto cross-check against independent implementations",
                 [python_exe(), TESTTOOL / "host" / "crypto_ref" / "run_checks.py",
                  "--rounds", "8"], cwd=tool_repo)

        run_step("DG1", "downgrade guard: older image refused, and not uploaded",
                 [python_exe(), TESTTOOL / "host" / "fakeboard" / "run_downgrade.py"],
                 cwd=tool_repo)

        run_step("P4", "Arduino variant assertions (the FMC reserved-pin table)",
                 [python_exe(), TESTTOOL / "host" / "variant_check" / "build.py"],
                 needs=getattr(cfg, "ARDUINO_CLI", ""), cwd=tool_repo)

    # -------------------------------------------------------------- summary
    Section("summary")
    width = max(len(r["name"]) for r in results)
    idw = max(len(r["id"]) for r in results)
    for r in results:
        line = "%-*s %-*s  %s" % (idw, r["id"], width, r["name"], r["state"])
        if r["note"]:
            line += " (%s)" % r["note"]
        {"PASS": Ok, "SKIP": Warn, "FAIL": Fail}[r["state"]](line)

    failed = sum(1 for r in results if r["state"] == "FAIL")
    skipped = sum(1 for r in results if r["state"] == "SKIP")

    print("")
    if failed > 0:
        Fail("%d failed - do not go to the board until these are green" % failed)
        return 1
    if skipped > 0:
        Warn("%d skipped - those areas are unverified on this machine" % skipped)
    Ok("host-side checks pass; next is acceptance/checklist.md CHK-A4 (build) and CHK-A5 (flash)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
