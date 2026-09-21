"""T1-29..T1-32 -- replacing the bootloader in place.

    python3 tools/run_flashboot.py --bin <boot.bin> --key <owner.pem>
    python3 tools/run_flashboot.py --bin <boot.bin> --key <owner.pem> --sign-with-leaf
    python3 tools/run_flashboot.py --unclaimed --bin <boot.bin> --key <owner.pem>

WHAT EACH MODE PROVES

  default          T1-29 the board runs the new bootloader afterwards, and
                   T1-31 it still reports the same owner and generation
  --sign-with-leaf T1-30 an image signed by a leaf is refused, and sector 0
                   is untouched -- the board still boots the old bootloader
  --unclaimed      T1-32 a board with no owner refuses unless BOOT0 was held

The two refusal flags only change what this script expects; they do not set
the board up. For --sign-with-leaf, pass a leaf key as --key. For --unclaimed,
factory-reset the board first (BOOT0 gesture) and do NOT hold BOOT0 on the
boot you then test.

⚠️ DESTRUCTIVE, AND NOT RECOVERABLE WITHOUT AN ST-LINK. A failure between the
erase and the last write leaves a board that does not boot. That is inherent
to replacing a bootloader with itself, not a defect in this script; see
$PROD/docs/modules/M1/FLASHBOOT.md.

Exit code is the verdict: 0 the case holds, 1 it does not, 2 setup missing.
"""

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Warn, Fail, close_ports,  # noqa: E402
                    get_iap_tool, get_programmer_cli, get_scratch_file,
                    open_log_ports, run_capture, run_while_draining)

# IAP_config.h builds this from OPENPLC_FW_VERSION; the bootloader prints it
# on every boot, which is the only evidence that the new image is the one
# running.
BANNER_RE = re.compile(r"Boot Loader[ ]+([0-9][0-9A-Za-z._-]*)")
# owner.go's getowner output. Both fields have to be unchanged for T1-31.
OWNER_RE = re.compile(r"generation[ ]+([0-9]+)", re.I)
ROOT_RE = re.compile(r"([0-9a-fA-F]{128})")

TAIL_S = 8


def board_banner(ports, seconds):
    """Reset over SWD and return what the board printed while booting."""
    open_ports = open_log_ports(ports)
    rc, buf = run_while_draining([str(get_programmer_cli()), "-c", "port=SWD",
                                  "mode=UR", "-rst"],
                                 open_ports,
                                 get_scratch_file("flashboot_reset.out"),
                                 get_scratch_file("flashboot_reset.err"),
                                 tail_seconds=seconds)
    close_ports(open_ports)
    return "\n".join(buf.values())


def owner_fingerprint(ip, key):
    """(generation, root pubkey) as the board reports them, or (None, None)."""
    out, rc = run_capture([get_iap_tool(), "getowner", ip, "--key=" + key])
    if rc != 0:
        Warn("  getowner exit %d" % rc)
        return None, None
    gen = OWNER_RE.search(out)
    root = ROOT_RE.search(out)
    return (gen.group(1) if gen else None), (root.group(1) if root else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", required=True, help="the bootloader image to install")
    ap.add_argument("--key", default="", help="owner root private key (PEM)")
    ap.add_argument("--ip", default="")
    ap.add_argument("--ports", nargs="*", default=None)
    ap.add_argument("--tail-seconds", type=int, default=TAIL_S)
    ap.add_argument("--sign-with-leaf", action="store_true",
                    help="T1-30: --key is a leaf, not the root; expect a refusal")
    ap.add_argument("--unclaimed", action="store_true",
                    help="T1-32: board already factory-reset, BOOT0 not held; expect a refusal")
    args = ap.parse_args()

    image = Path(args.bin)
    if not image.exists():
        Fail("no such image: %s" % image)
        return 2
    size = image.stat().st_size
    if size > 120 * 1024:
        Fail("%d bytes will not fit the 120 KiB bootloader region" % size)
        return 1
    ip = args.ip or getattr(cfg, "BOARD_IP", "")
    if not ip:
        Fail("need --ip (or set BOARD_IP in config)")
        return 2
    if not args.key:
        Fail("need --key: the board checks a bootloader image against the owner root")
        return 2
    ports = list(args.ports if args.ports is not None else cfg.LOG_PORTS)
    expect_refusal = args.sign_with_leaf or args.unclaimed

    Section("Before")
    before = board_banner(ports, args.tail_seconds)
    print(before)
    old = BANNER_RE.search(before)
    if not old:
        Fail("the board never printed its bootloader banner -- nothing to compare against")
        return 1
    Ok("  board runs Boot Loader %s" % old.group(1))

    gen_before = root_before = None
    if not args.unclaimed:
        gen_before, root_before = owner_fingerprint(ip, args.key)
        print("  owner: generation %s, root %s" % (gen_before, (root_before or "")[:16]))

    Section("flashboot")
    if expect_refusal:
        Warn("  this run expects the board to REFUSE; a success is the failure")
    cmd = [get_iap_tool(), "flashboot", str(image), ip, "--key=" + args.key]
    open_ports = open_log_ports(ports)
    rc, buf = run_while_draining(cmd, open_ports,
                                 get_scratch_file("flashboot.out"),
                                 get_scratch_file("flashboot.err"),
                                 tail_seconds=args.tail_seconds)
    close_ports(open_ports)
    during = "\n".join(buf.values())
    print(during)

    Section("After")
    after = board_banner(ports, args.tail_seconds)
    print(after)
    new = BANNER_RE.search(after)
    fails = 0

    if expect_refusal:
        # The board has to still be the board: same banner, and it said why.
        if not new:
            Fail("  the board no longer boots -- a refused flashboot must not touch sector 0")
            return 1
        if new.group(1) != old.group(1):
            Fail("  bootloader changed from %s to %s after a refusal" % (old.group(1), new.group(1)))
            fails += 1
        else:
            Ok("  sector 0 untouched: still Boot Loader %s" % new.group(1))
        why = "Refused" if args.unclaimed else "Signature Failed"
        if why.lower() in during.lower():
            Ok("  the board answered %r" % why)
        else:
            Fail("  expected %r in the exchange; the refusal has to say why" % why)
            fails += 1
    else:
        if not new:
            Fail("  the board does not boot after the upgrade")
            return 1
        Ok("  board runs Boot Loader %s" % new.group(1))
        gen_after, root_after = owner_fingerprint(ip, args.key)
        if gen_after == gen_before and root_after == root_before and gen_after is not None:
            Ok("  ownership survived: generation %s, same root" % gen_after)
        else:
            Fail("  ownership changed: generation %s -> %s, root %s -> %s"
                 % (gen_before, gen_after, (root_before or "")[:16], (root_after or "")[:16]))
            fails += 1

    if fails:
        Fail("%d check(s) failed" % fails)
        return 1
    Ok("flashboot behaved as specified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
