"""T2-07 -- a board built with its own root does not warn, and still runs.

    python3 tools/run_custom_root_has_no_warning.py
    python3 tools/run_custom_root_has_no_warning.py --bin <app.bin> --key <root.pem>

This is path ③ of the five-path run: the customer rotated the root compiled
into the bootloader (`rotate_keys.sh`), rebuilt and flashed it. From then on
the board trusts a key only they hold, and the published-root warning must
stop appearing.

⚠️ THE WARNING IS NOT KEYED ON THE OWNER SLOT BEING EMPTY. A customer who
compiled with their own key has an empty slot and a perfectly safe board.
`report_root_trust()` compares the root in force against a SHA-256 of the
published key, so this case has to see BOTH halves at once:

    "Owner slot: empty"                        present
    "trusts the PUBLISHED root key"            absent

Either half alone proves nothing. An empty slot with the warning still there
is a board that was never rotated; an absent warning on its own is what a log
that was never captured also looks like.

That last point is why a capture-proof line is required before any absence is
believed: a boot log nobody caught contains zero warnings too. T2-07 has been
bitten by exactly this before.

With --bin and --key it also runs the second half of the case: an application
signed by the new root uploads and starts. Without them it judges the boot
log only.

Exit code is the verdict: 0 the case holds, 1 it does not, 2 the evidence was
never captured (INCONCLUSIVE -- not a pass, not a failure).
"""

import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (Fail, Ok, Section, Warn, cfg, close_ports,  # noqa: E402
                    get_programmer_cli, get_scratch_file, open_log_ports,
                    python_exe, run_while_draining)

# Always printed, whatever the board decides, so its absence means the capture
# failed rather than the board staying quiet. Deliberately not the SDRAM
# self-test line: that one only appears when the board stays in the
# bootloader, so on a board with a working app its absence is ambiguous.
LOG_CAPTURE_PROOF = "Bootloader state:"
LOG_OWNER_EMPTY = "Owner slot: empty"
LOG_PUBLISHED_WARNING = "trusts the PUBLISHED root key"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ip", default="")
    ap.add_argument("--bin", help="application signed by the new root; "
                                  "runs the second half of the case")
    ap.add_argument("--key", help="the new root's private key, for --bin")
    ap.add_argument("--expect-banner", help="passed through to upload_and_watch.py")
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--ports", nargs="*", default=None)
    args = ap.parse_args()

    ports = list(args.ports if args.ports is not None else cfg.LOG_PORTS)

    Section("boot log")
    handles = open_log_ports(ports)
    _, buf = run_while_draining([str(get_programmer_cli()), "-c", "port=SWD",
                                 "mode=UR", "-rst"],
                                handles,
                                get_scratch_file("custom_root.out"),
                                get_scratch_file("custom_root.err"),
                                tail_seconds=args.seconds)
    close_ports(handles)
    log = "\n".join(buf.values())
    print(log)

    Section("verdict")
    if LOG_CAPTURE_PROOF not in log:
        Fail("nothing was captured (%r never appeared)" % LOG_CAPTURE_PROOF)
        Warn("  an absent warning and an absent log look identical -- this is")
        Warn("  INCONCLUSIVE, not a pass. Check the log port and run again.")
        return 2

    fails = 0
    if LOG_OWNER_EMPTY in log:
        Ok("  %r -- the board was not claimed, so the root is the compiled-in one"
           % LOG_OWNER_EMPTY)
    else:
        Fail("  %r is missing: this board is claimed, so it is not in path 3's state"
             % LOG_OWNER_EMPTY)
        Warn("  a claimed board does not warn either, for a different reason --")
        Warn("  that would pass the half below while proving nothing about rotation")
        fails += 1

    if LOG_PUBLISHED_WARNING in log:
        Fail("  the published-root warning is still there: the root in force is "
             "still the one shipped with the project")
        fails += 1
    else:
        Ok("  the published-root warning is gone")

    if args.bin:
        Section("and an application signed by the new root still runs")
        argv = [python_exe(), str(HERE / "upload_and_watch.py"), "--bin", args.bin]
        if args.ip:
            argv += ["--ip", args.ip]
        if args.key:
            argv += ["--key", args.key]
        if args.expect_banner:
            argv += ["--expect-banner", args.expect_banner]
        if args.ports is not None:
            argv += ["--ports"] + ports
        if subprocess.run(argv).returncode != 0:
            Fail("  the upload did not pass; see its own output above")
            fails += 1
        else:
            Ok("  it uploaded and started")

    if fails:
        Fail("%d check(s) failed" % fails)
        return 1
    Ok("T2-07 holds: own root, no warning, and the board still takes firmware")
    return 0


if __name__ == "__main__":
    sys.exit(main())
