"""Which relay makes the start-up window noise, over many boots.

    python3 tools/run_relay_pick_distribution.py --resets 30

The bootloader draws one of RY1..RY6 for the start-up window so the contact
wear spreads instead of always landing on the same three. "Drawn at random"
is easy to write and easy to get wrong: srand() seeded from a value with
little jitter, or a modulus taken off the first output of a freshly seeded
generator, both deal the same number far more often than one time in six.
This resets the board repeatedly and counts what it actually deals.

It reads the line the bootloader prints before it clicks:

    Startup window: RY3

⚠️ This resets over SWD, which is NOT a power-on. A brownout caused by the
coil's inrush would not reproduce here, and neither would anything else that
depends on the supply coming up. For that, power-cycle by hand and watch
tools/serial_watch.py instead. What this does cover is the draw itself.

It also counts boot banners per reset: more than one means the board started
over on its own, which is worth knowing whatever the distribution looks like.

Exit code is the verdict: 0 every relay came up at least once and no boot
restarted itself, 1 otherwise. A skewed but complete draw is reported and
still exits 0 -- how uneven is too uneven is a judgement, not a threshold
this script should invent.
"""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (Fail, Ok, Section, Warn, cfg, close_ports,  # noqa: E402
                    get_programmer_cli, get_scratch_file, open_log_ports,
                    run_while_draining)

PICK_RE = re.compile(r"Startup window: RY(\d)")
BANNER_RE = re.compile(r"Checking Starting Mod")
RELAYS = 6


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resets", type=int, default=30)
    ap.add_argument("--seconds", type=float, default=4.0,
                    help="how long to listen after each reset; the window "
                         "itself is 2 s, so this has to outlast it")
    ap.add_argument("--ports", nargs="*", default=None)
    args = ap.parse_args()

    ports = list(args.ports if args.ports is not None else cfg.LOG_PORTS)
    picks = Counter()
    restarts = 0
    unreadable = 0

    Section("resetting %d times" % args.resets)
    for n in range(1, args.resets + 1):
        handles = open_log_ports(ports)
        _, buf = run_while_draining([str(get_programmer_cli()), "-c", "port=SWD",
                                     "mode=UR", "-rst"],
                                    handles,
                                    get_scratch_file("relay_pick.out"),
                                    get_scratch_file("relay_pick.err"),
                                    tail_seconds=args.seconds)
        close_ports(handles)
        text = "\n".join(buf.values())

        found = PICK_RE.findall(text)
        banners = len(BANNER_RE.findall(text))
        if not found:
            unreadable += 1
            Warn("  %2d: no 'Startup window' line" % n)
            continue
        for d in found:
            picks[int(d)] += 1
        if banners > 1 or len(found) > 1:
            restarts += 1
            Warn("  %2d: RY%s -- %d boot(s) in one reset, the board started over"
                 % (n, "+RY".join(found), banners))
        else:
            print("  %2d: RY%s" % (n, found[0]))

    Section("what was dealt")
    total = sum(picks.values())
    if total == 0:
        Fail("nothing was read -- is the log port right?")
        return 2
    for relay in range(1, RELAYS + 1):
        c = picks.get(relay, 0)
        bar = "#" * c
        print("  RY%d  %3d  %5.1f%%  %s" % (relay, c, 100.0 * c / total, bar))
    print("  %d draw(s) over %d reset(s); even would be %.1f%% each"
          % (total, args.resets, 100.0 / RELAYS))

    Section("verdict")
    problems = 0
    missing = [r for r in range(1, RELAYS + 1) if picks.get(r, 0) == 0]
    if missing:
        Fail("  never dealt: %s -- those contacts take none of the wear"
             % ", ".join("RY%d" % r for r in missing))
        problems += 1
    else:
        Ok("  all six came up at least once")
    if restarts:
        Fail("  %d reset(s) produced more than one boot: the board restarted "
             "itself, which is a fault of its own" % restarts)
        problems += 1
    else:
        Ok("  every reset produced exactly one boot")
    if unreadable:
        Warn("  %d reset(s) printed no pick line at all" % unreadable)

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
