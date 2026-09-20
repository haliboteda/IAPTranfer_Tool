"""T2-08 -- the published-root warning is on EVERY boot, not a one-off notice.

    python tools/run_public_root_warning_is_persistent.py
    python tools/run_public_root_warning_is_persistent.py --resets 5
    python tools/run_public_root_warning_is_persistent.py --seconds 10

An unclaimed board trusts the root whose private half ships in the repository,
so anyone can sign firmware it will run. That warning is the ONLY way a customer
ever learns this, and its whole value is that it does not go away: a notice
printed once gets scrolled past, and a warning people learn to ignore protects
nobody. So "it appeared" is not the claim being tested -- "it appears every
single time" is.

⚠️ PRECONDITION: the board must be UNCLAIMED. On a claimed board the warning is
correctly absent, so this reports SETUP (exit 2), not failure. Put the board
back with tools/reset_board_to_factory_state.py.

No human needed: resets are driven over ST-Link.

Each round needs a proof-of-capture line as well as the warning. Without it an
empty capture and a board that stayed silent look identical -- the same trap
T2-07 documents.

Exit 0 = warning on every reset, 1 = missed at least one, 2 = setup problem.
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (Fail, Ok, Section, Warn, assert_target_reachable,  # noqa: E402
                    cfg, get_programmer_cli, open_log_ports, read_log_ports)

WARNING = "trusts the PUBLISHED root key"
# Printed on every boot whatever the ownership state, so its absence means the
# capture failed rather than the board having nothing to say.
CAPTURE_PROOF = "Bootloader state:"
# Present only while the board is unclaimed; this is the precondition, not the
# thing under test.
UNCLAIMED = "Owner slot: empty"


def one_round(cli, ports, seconds):
    """Reset once and return the captured text."""
    open_ports = open_log_ports(ports)
    subprocess.run([str(cli), "-c", "port=SWD", "mode=UR", "-rst"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "\n".join(read_log_ports(open_ports, seconds).values())


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--resets", type=int, default=3,
                    help="how many consecutive boots must show the warning")
    ap.add_argument("--seconds", type=int, default=8)
    ap.add_argument("--ports", nargs="*", default=None)
    args = ap.parse_args()

    ports = args.ports if args.ports else cfg.LOG_PORTS
    cli = get_programmer_cli()

    Section("Target check")
    assert_target_reachable(cli)

    results = []
    for n in range(1, args.resets + 1):
        Section("Reset %d of %d" % (n, args.resets))
        log = one_round(cli, ports, args.seconds)

        captured = CAPTURE_PROOF in log
        warned = WARNING in log
        unclaimed = UNCLAIMED in log

        if not captured:
            Warn("  no boot log captured (%d bytes)" % len(log))
        else:
            Ok("  boot log captured")
            Ok("  board is unclaimed") if unclaimed else Warn("  board is NOT unclaimed")
            Ok("  warning present") if warned else Fail("  warning MISSING")
        results.append((captured, warned, unclaimed))

    Section("T2-08 verdict")

    if not any(c for c, _, _ in results):
        Warn("SETUP - not one boot log was captured; nothing was proved.")
        Warn("  Check the log ports above before reading anything into this run.")
        return 2
    if not all(u for c, _, u in results if c):
        Warn("SETUP - the board is claimed, so the warning is correctly absent.")
        Warn("  This case only applies to an unclaimed board. Put it back with:")
        Warn("    python tools/reset_board_to_factory_state.py")
        return 2

    missed = [i + 1 for i, (c, w, _) in enumerate(results) if not (c and w)]
    if missed:
        Fail("FAIL - the warning did not survive every reset.")
        Fail("  Missing on reset(s): %s of %d." % (missed, args.resets))
        Fail("  A warning that fades is a warning nobody will act on.")
        return 1

    Ok("PASS - the warning appeared on all %d consecutive boots." % args.resets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
