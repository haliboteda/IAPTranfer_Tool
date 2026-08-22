"""M7 step 5 acceptance: run a board script and its Python translation against the
SAME board and check they reach the same verdict.

    python tools/m7_compare_board.py                 every pair
    python tools/m7_compare_board.py --only t0       one pair
    python tools/m7_compare_board.py --list          just list them
    python tools/m7_compare_board.py --keep          keep both captures on disk

Why this is not m7-compare.ps1
------------------------------
The host-side harness compares stdout byte for byte. That is impossible here and
would be the wrong criterion anyway: a board script prints captured serial, which
carries a different tick count, a different DHCP lease, a different byte count and
a different nonce counter on every single run. Diffing those trains people to
ignore the diff -- the exact failure mode M7 was written to avoid.

So the criterion is the one M7 wrote for step 5: the same verdict on the same
board. Concretely, for each pair:

  * the exit codes must be equal, and
  * a list of SIGNALS -- regexes naming the branches a reader would act on -- must
    be present-or-absent identically on both sides.

Signals are per branch, never an alternation. "PASS or INCONCLUSIVE appeared" is
satisfied by either, so it would pass a run where one version said PASS and the
other said INCONCLUSIVE; two separate signals catch it.

⚠️ Order matters and the table is ordered deliberately. Some pairs leave the board
somewhere else than they found it, so each case declares the state it needs and
the runner establishes it before EACH side runs -- otherwise the second side is
not being asked the same question as the first.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (Fail, Ok, Section, Warn, cfg, get_scratch_file,  # noqa: E402
                    python_exe, wait_for_board)

# state: what the board must be in before each side runs.
#   "any"        -- the script does not care
#   "app"        -- an application installed and booting (reset is enough)
#   "bootloader" -- parked in the bootloader via enter_bootloader
CASES = [
    {
        "name": "serial-watch",
        "state": "any",
        "ps1": ["serial-watch.ps1", "-Seconds", "6"],
        "py": ["serial_watch.py", "--seconds", "6"],
        "signals": [
            r"listening on COM",
            r"===== Capturing 6s",
            r"cannot open",
        ],
    },
    {
        "name": "t0-reset",
        "state": "app",
        "ps1": ["flash-bootloader.ps1", "-ResetOnly", "-Seconds", "10"],
        "py": ["flash_bootloader.py", "--reset-only", "--seconds", "10"],
        "signals": [
            r"target voltage",
            r"===== BG1 verdict",
            r"PASS - staging buffer usable",
            r"FAIL - SDRAM self-test failed",
            r"INCONCLUSIVE - the board booted its application",
            r"No serial output at all",
        ],
    },
    {
        "name": "enter-bootloader",
        "state": "app",
        "ps1": ["enter-bootloader.ps1"],
        "py": ["enter_bootloader.py"],
        "signals": [
            r"===== Requesting bootloader",
            r"board is in the bootloader",
            r"board entered upload mode, but the refusal line was not seen",
            r"board does not appear to be in the bootloader",
            r"UPLOAD Mod",
        ],
    },
]


def run_side(label, argv, keep_as):
    """Run one side, save its output, return (text, exit code)."""
    proc = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace", cwd=str(HERE.parent))
    text = proc.stdout or ""
    keep_as.write_text(text, encoding="utf-8", errors="replace")
    print("  %-3s exit %-3d %6d char(s)  -> %s" % (label, proc.returncode, len(text), keep_as))
    return text, proc.returncode


def establish(state):
    """Put the board where this case expects to find it.

    ⚠️ Every reset here is followed by wait_for_board(). Without it the script
    under test starts talking while the board is still bringing its link up, gets
    "No response, exiting", and fails for a reason that has nothing to do with
    the case. That failure is symmetric -- BOTH sides get it -- so the comparison
    happily reports "same verdict" while having tested nothing. The first run of
    this harness did exactly that on the enter-bootloader pair.
    """
    if state == "any":
        return True

    if state == "app":
        # A plain reset: if an application is installed it boots, and that is the
        # state "app" means. Nothing here asserts one IS installed -- the case's
        # own signals cover which branch was taken.
        from common import get_programmer_cli
        subprocess.run([str(get_programmer_cli()), "-c", "port=SWD", "mode=UR", "-rst"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ip = getattr(cfg, "BOARD_IP", "")
        if ip and not wait_for_board(ip, timeout=60):
            Warn("  board did not answer discovery within 60s after reset")
            return False
        return True

    if state == "bootloader":
        if not establish("app"):
            return False
        rc = subprocess.run([python_exe(), str(HERE / "enter_bootloader.py")],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode
        if rc != 0:
            Warn("  could not park the board in the bootloader (exit %d)" % rc)
            return False
        return True

    raise ValueError("unknown state %r" % state)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if args.list:
        Section("board pairs")
        for c in CASES:
            print("  %-18s state=%-10s %s  vs  %s"
                  % (c["name"], c["state"], c["ps1"][0], c["py"][0]))
        return 0

    cases = [c for c in CASES if not args.only or c["name"] == args.only]
    if not cases:
        Fail("no pair named '%s'" % args.only)
        Warn("  run with --list to see them")
        return 2

    bad = 0
    for c in cases:
        Section("board compare: %s" % c["name"])

        ps1 = HERE / c["ps1"][0]
        py = HERE / c["py"][0]
        for f in (ps1, py):
            if not f.exists():
                Fail("missing: %s" % f)
                bad += 1
        if not ps1.exists() or not py.exists():
            continue

        a_file = get_scratch_file("m7b-%s.ps1.out" % c["name"])
        b_file = get_scratch_file("m7b-%s.py.out" % c["name"])

        if not establish(c["state"]):
            Fail("could not establish state '%s' -- not comparing" % c["state"])
            bad += 1
            continue
        a, a_code = run_side("ps1", ["powershell", "-NoProfile", "-ExecutionPolicy",
                                     "Bypass", "-File", str(ps1)] + c["ps1"][1:], a_file)

        if not establish(c["state"]):
            Fail("could not re-establish state '%s' -- the two sides were asked "
                 "different questions" % c["state"])
            bad += 1
            continue
        b, b_code = run_side("py", [python_exe(), str(py)] + c["py"][1:], b_file)

        problems = []
        if a_code != b_code:
            problems.append("exit code differs: ps1=%d py=%d" % (a_code, b_code))
        elif a_code != 0 and not c.get("both_may_fail"):
            # Two failures agreeing is not evidence of anything. The usual cause
            # is the board not being in the state the case needs, and it is
            # symmetric -- so without this rule the harness reports green for a
            # pair that tested nothing. Set both_may_fail on a case whose normal
            # outcome really is a non-zero exit.
            problems.append("BOTH sides failed the case itself (exit %d) -- parity "
                            "between two failures proves nothing; fix the setup first"
                            % a_code)

        for sig in c["signals"]:
            in_a = re.search(sig, a) is not None
            in_b = re.search(sig, b) is not None
            if in_a != in_b:
                problems.append("signal /%s/ : ps1=%s py=%s"
                                % (sig, "yes" if in_a else "no", "yes" if in_b else "no"))

        if problems:
            bad += 1
            Fail("verdicts differ")
            for p in problems:
                print("    %s" % p)
            print("    both captures kept:")
            print("      %s" % a_file)
            print("      %s" % b_file)
        else:
            hit = [s for s in c["signals"] if re.search(s, a)]
            Ok("same verdict, exit %d, %d/%d signal(s) present"
               % (a_code, len(hit), len(c["signals"])))
            for s in hit:
                print("    /%s/" % s)
            if not args.keep:
                for f in (a_file, b_file):
                    try:
                        f.unlink()
                    except OSError:
                        pass

    Section("result")
    if bad:
        Fail("%d pair(s) reached a different verdict -- the Python version is NOT "
             "a drop-in yet" % bad)
        return 1
    Ok("all %d board pair(s) reach the same verdict on this board" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
