"""Every check that needs no board, in one command.

This is layer 1 of the acceptance checklist ($PROD/docs/tables/ACCEPTANCE-CHECKLIST.md,
section A).
It is cheap enough to run after every edit, and each round of on-board debugging
costs an order of magnitude more -- so nothing here should ever be skipped on the
way to the board.

    python tools/selfcheck.py              run everything
    python tools/selfcheck.py --quick      skip the slow ones (fakeboard, C unit tests)
    python tools/selfcheck.py --list       say what each step is, run nothing

Exit 0 = all pass, 1 = at least one failed. A check whose prerequisite is absent
(no gcc, no arduino-cli) reports SKIP and does not fail the run -- but the summary
always names it, because a silently skipped check reads as a pass.

Step ids ARE the case ids: a step number of its own would be an alias for a case
that already has an id, and "A7 passed" would have several possible meanings.

Each step announces what requirement it covers, because "A12 passed" told you
nothing about what is now known to work.

Everything prints in the order things actually happen: a child's output belongs
before the banner that judges it, not after.

T1-18a-T1-18g / T1-19-T1-20 do not gate on finding "python" on PATH. This interpreter is
what runs them, so there is nothing to look up -- gating on the literal "python"
is what would make those three SKIP on a python3-only machine.
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (Fail, Ok, Section, Warn, cfg, docs_repo,  # noqa: E402
                    have_cmd, probe, python_exe)

TESTTOOL = HERE.parent
results = []

# What each step is, in run order. This is the ONE place the step list lives:
# --list prints it, and run_step() looks up "covers" here and raises if a step is
# missing, so the two can never drift apart.
#
# "covers" is the requirement id in $PROD/docs/tables/STATUS.md that this
# case is the evidence for. A case that covers nothing should not exist.
CATALOG = [
    ("ENV",          "-",                      "this machine has the toolchain"),
    ("T1-15",        "R1-20",                  "host Go tests (certificate issuance, serial counter, challenge signing)"),
    ("H3",           "-",                      "go vet over the whole module"),
    ("P1",           "ENG-02",                 "firmware version agrees in all three places"),
    ("P2",           "ENG-03 R1-06 R1-07 R2-01 R1-14 R3-04", "cross-repo mirrored code has not diverged"),
    ("P3",           "ENG-04",                 "Arduino core: live matches the git repo"),
    ("P11",          "ENG-05",                 "the packaged IAPTool is not behind the repository"),
    ("T2-06",        "R2-02",                  "the published-root warning still recognises the published root"),
    ("P7",           "-",                      "every cited case is defined, and every defined case is cited"),
    ("P8",           "-",                      "no claim is written out in more than one document"),
    ("P14",          "-",                      "no unfinished work lives only in a map's CHANGE-LIST"),
    ("P9",           "-",                      "every path a document names actually exists"),
    ("P13",          "-",                      "no renamed id is still cited anywhere"),
    ("P12",          "-",                      "OpenPLC_Docs: tickets close honestly, placeholders have owners"),
    ("T1-16",        "R1-20",                  "host C unit tests (real sha256.c / iap_keyderive.c / iap_auth.c)"),
    ("T2-21",        "R2-04",                  "the root in force cannot revoke itself (real owner_root_ro.c over a fake record area)"),
    ("T2-22-T2-23",  "R2-04",                  "revocation area: warns at 8 slots left, refuses the 97th without writing"),
    ("T1-33",        "R1-36",                  "flashboot compaction keeps the chain and the live revocations, drops the rest"),
    ("T2-24",        "R2-04",                  "setowner --wipe judges the handover before erasing, and reclaims every slot"),
    ("T2-27",        "R2-04",                  "a revocation naming the root in force is ignored (real owner_slot.c, the bootloader's own copy of R4)"),
    ("T4-01",        "-",                      "port tool protocol contract (real porttool.c, then the Go parser)"),
    ("T1-18a-T1-18g", "R1-21",                  "IAPTool key/certificate match against a stand-in board"),
    ("T1-19-T1-20",  "R1-24",                  "crypto cross-check against independent implementations"),
    ("P4",           "R3-04",                  "Arduino variant assertions (FMC reserved pins, UART routing)"),
    ("P15",          "R1-33",                  "the application's start address stays 1024-aligned"),
    ("P16",          "R1-25",                  "the I-cache and the flash lock are restored on every exit"),
    ("P17",          "-",                      "the .cproject linker script is still the ${PLC_LD_SCRIPT} variable"),
]
COVERS = {cid: covers for cid, covers, _ in CATALOG}

STATUS_DOC = "$PROD/docs/tables/STATUS.md"
CRITERIA_DOC = "$PROD/docs/engineering/HOW-TO-RUN-TESTS.md"


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
    print("  %d steps. --quick skips T1-16 / T2-21 / T2-22-T2-23 / T1-33 / T2-24 / T4-01 / T1-18a-T1-18g / T1-19-T1-20 / P4 / P15."
          % len(CATALOG))
    print("  P7, P8 and P9 check the documents, not the firmware.")


def record(step_id, name, state, note=""):
    results.append({"id": step_id, "name": name, "state": state, "note": note})


def run_step(step_id, name, argv, needs=None, cwd=None, indent=0):
    """One step: announce what it is, run it, record PASS/SKIP/FAIL.

    needs is a command name or an absolute path -- a tool installed for one check
    only (HOST_CC) has no business being on PATH.

    indent shifts a child's output so it reads as belonging to the step. The
    Python suites format their own output and are left alone.
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

    run_step("T1-15", "host Go tests (certificate issuance, serial counter, challenge signing)",
             ["go", "test", "./TestCase/..."], needs="go", cwd=tool_repo, indent=2)

    run_step("H3", "go vet over the whole module",
             ["go", "vet", "./..."], needs="go", cwd=tool_repo, indent=2)

    run_step("P1", "firmware version agrees in all three places",
             [python_exe(), HERE / "check_version_sync.py"], cwd=tool_repo)

    run_step("P2", "cross-repo mirrored code has not diverged",
             [python_exe(), HERE / "check_mirror_sync.py"], cwd=tool_repo)

    run_step("P3", "Arduino core: live matches the git repo",
             [python_exe(), HERE / "check_core_sync.py"], cwd=tool_repo)

    run_step("P11", "the packaged IAPTool is not behind the repository",
             [python_exe(), HERE / "check_tool_sync.py"], cwd=tool_repo)

    run_step("T2-06", "the published-root warning still recognises the published root",
             [python_exe(), HERE / "check_public_root.py"], cwd=tool_repo)

    # These two guard the documents rather than the product. They are here because
    # a table that has drifted from the cases, or a fact claimed in two files, is
    # exactly as expensive to find later as a code divergence -- and 2026-08-22
    # proved nobody finds either by eye.
    run_step("P7", "every cited case is defined, and every defined case is cited",
             [python_exe(), HERE / "check_status_sync.py"], cwd=tool_repo)

    run_step("P8", "no claim is written out in more than one document",
             [python_exe(), HERE / "check_doc_dupes.py"], cwd=tool_repo)

    run_step("P14", "no unfinished work lives only in a map's CHANGE-LIST",
             [python_exe(), HERE / "check_changelist_has_no_orphans.py"], cwd=tool_repo)

    run_step("P9", "every path a document names actually exists",
             [python_exe(), HERE / "check_doc_paths.py"], cwd=tool_repo)

    # P7 compares two name lists and P9 resolves paths, so neither can see an id
    # left behind in prose or in a source comment. This is the only thing that can.
    run_step("P13", "no renamed id is still cited anywhere",
             [python_exe(), HERE / "check_no_stale_ids.py"], cwd=tool_repo)

    # Reads the bootloader source, so it belongs with the always-run checks
    # rather than behind --quick: it needs no toolchain, only the .c files.
    run_step("P16", "the I-cache and the flash lock are restored on every exit",
             [python_exe(), HERE / "check_icache_is_restored.py"], cwd=tool_repo)

    run_step("P17", "the .cproject linker script is still the ${PLC_LD_SCRIPT} variable",
             [python_exe(), HERE / "check_cproject_ld.py"], cwd=tool_repo)

    # The pre-commit hook in OpenPLC_Docs runs these too, but --no-verify skips
    # it and core.hooksPath is not under version control, so the gate lives here.
    docs = docs_repo()
    if docs:
        run_step("P12", "OpenPLC_Docs: tickets close honestly, placeholders have owners",
                 [python_exe(), docs / "tools" / "check_wayfinder_ticket_hygiene.py"],
                 cwd=docs)
        run_step("P12", "OpenPLC_Docs: no placeholder without a ticket",
                 [python_exe(), docs / "tools" / "check_no_orphan_placeholders.py"],
                 cwd=docs)

    if not args.quick:
        # HOST_CC from config wins; otherwise fall back to whatever "gcc"
        # resolves to on PATH, so a machine with neither still reports SKIP by
        # name.
        cc_need = getattr(cfg, "HOST_CC", "") or "gcc"

        run_step("T1-16", "host C unit tests (real sha256.c / iap_keyderive.c / iap_auth.c)",
                 [python_exe(), TESTTOOL / "host" / "bootloader_unit" / "build.py"],
                 needs=cc_need, cwd=tool_repo)

        # The owner slot T1-16 sees is a stub, so the real resolve_chain() runs
        # nowhere else on the host. This is the only step that exercises R4.
        run_step("T2-21", "the root in force cannot revoke itself (real owner_root_ro.c over a fake record area)",
                 [python_exe(), TESTTOOL / "host" / "owner_revoke" / "build.py"],
                 needs=cc_need, cwd=tool_repo)

        # The only step that compiles owner_slot.c itself, so the only place
        # the WRITE path runs off the board. On a board these two cases would
        # burn all 96 revocation slots for good.
        run_step("T2-22-T2-23", "revocation area: warns at 8 slots left, refuses the 97th without writing",
                 [python_exe(), TESTTOOL / "host" / "owner_capacity" / "build.py", "capacity"],
                 needs=cc_need, cwd=tool_repo)

        # Same harness, other end of the area: what survives the erase during
        # a flashboot. Two phases, because the read-back has to happen in a
        # process that has not scanned the area yet.
        run_step("T1-33", "flashboot compaction keeps the chain and the live revocations, drops the rest",
                 [python_exe(), TESTTOOL / "host" / "owner_capacity" / "build.py", "compact"],
                 needs=cc_need, cwd=tool_repo)

        run_step("T2-24", "setowner --wipe judges the handover before erasing, and reclaims every slot",
                 [python_exe(), TESTTOOL / "host" / "owner_capacity" / "build.py", "wipe"],
                 needs=cc_need, cwd=tool_repo)

        # The bootloader's own copy of R4. T2-21 covers the core mirror's copy;
        # until this step there was no coverage of this one off a board, and
        # owner_slot_revoke() refuses to write the record that reaches it.
        run_step("T2-27", "a revocation naming the root in force is ignored, so no board can be revoked into a brick",
                 [python_exe(), TESTTOOL / "host" / "owner_capacity" / "build.py", "self-revoke"],
                 needs=cc_need, cwd=tool_repo)

        # build.py runs both halves: the C harness against the real firmware
        # source, then the Go test over the transcript it just wrote. They are
        # one step because running either alone lets the two drift apart, which
        # is the failure this case exists to prevent.
        run_step("T4-01", "port tool protocol contract (real porttool.c, then the Go parser)",
                 [python_exe(), TESTTOOL / "host" / "porttool_caps" / "build.py"],
                 needs=cc_need, cwd=tool_repo)

        run_step("T1-18a-T1-18g", "IAPTool key/certificate match against a stand-in board",
                 [python_exe(), TESTTOOL / "host" / "fakeboard" / "run_cases.py"],
                 cwd=tool_repo)

        run_step("T1-19-T1-20", "crypto cross-check against independent implementations",
                 [python_exe(), TESTTOOL / "host" / "crypto_ref" / "run_checks.py",
                  "--rounds", "8"], cwd=tool_repo)

        run_step("P4", "Arduino variant assertions (FMC reserved pins, UART routing)",
                 [python_exe(), TESTTOOL / "host" / "variant_check" / "build.py"],
                 needs=getattr(cfg, "ARDUINO_CLI", ""), cwd=tool_repo)

        run_step("P15", "the application's start address stays 1024-aligned",
                 [python_exe(), TESTTOOL / "host" / "vector_alignment" / "build.py"],
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
    Ok("host-side checks pass; next is ACCEPTANCE-CHECKLIST.md CHK-A4 (build) and CHK-A5 (flash)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
