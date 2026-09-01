"""OW1 -- claim a board for a signing key, and check it took (requirement C10).

    python3 tools/run_takeown.py                   claim with a freshly generated key
    python3 tools/run_takeown.py --key <128hex>    claim with a specific key
    python3 tools/run_takeown.py --expect-refused  the board should say no (negative case)

⚠️ THIS NEEDS SOMEBODY AT THE BOARD, and that is the whole point. takeown is
gated on BOOT0 having been held through the startup window: the first claim
carries no signature -- there is no owner yet to sign it -- so physical presence
is the only gate there can be. See docs/design/OWNERSHIP.md.

Before running: press RESET, then hold BOOT0 until the relays finish clicking
and let go. The board should be sitting in "UPLOAD Mod ... (BOOT0 held)".

⚠️ RECOVERY: claiming is meant to be hard to undo. Factory reset (M1 step 6) is
not implemented yet, so the only way back is to reflash the bootloader over
ST-Link -- the owner records live in the bootloader's own sector, so erasing it
to write the bootloader takes them with it:

    python3 tools/flash_bootloader.py

Exit 0 = the board ended up in the expected state, 1 = it did not, 2 = setup.
"""

import argparse
import re
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Fail, banner,  # noqa: E402
                    get_go_bin, run_capture, tcp_command)


def genkey(iap):
    """Run IAPTool genkey in a fresh directory and return (hexkey, directory)."""
    scratch = Path(tempfile.gettempdir()) / ("takeown-" + uuid.uuid4().hex[:8])
    scratch.mkdir(parents=True, exist_ok=True)
    out, _ = run_capture([iap, "genkey", "owner_key"], cwd=scratch)
    key = "".join(re.findall(r"0x([0-9a-fA-F]{2})", out)).lower()
    return key, scratch


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ip", default="")
    ap.add_argument("--port", default="56865")
    ap.add_argument("--key", default="")
    ap.add_argument("--expect-refused", action="store_true")
    args = ap.parse_args()

    ip = args.ip or getattr(cfg, "BOARD_IP", "")
    if not ip:
        Fail("need --ip (or set BOARD_IP in config/machine.py)")
        return 2

    # Said at run time, not only in the docstring at the top of this file: the
    # board has to ALREADY be in this state before the first command goes out,
    # and a requirement nobody sees is a requirement nobody meets.
    banner(["PRESS RESET, THEN HOLD BOOT0 UNTIL THE RELAYS STOP CLICKING.",
            "Let go. The board should print: UPLOAD Mod ... (BOOT0 held)"])

    print("  Nothing to confirm -- if BOOT0 was not held, the board answers Refused")
    print("  and this script says so. That refusal IS the check.")
    print()
    print("  Why physical presence: the first claim carries no signature (there is no")
    print("  owner yet to sign it), so a button is the only gate there can be.")
    print()

    Section("before")
    was = tcp_command(ip, args.port, "getpubkey")
    print("  getpubkey: %s" % was)
    if not re.fullmatch(r"[0-9a-fA-F]{128}", was):
        Fail("the board did not answer getpubkey with a key -- is it in the bootloader?")
        return 2

    key = args.key
    if not key:
        Section("generating a key to claim with")
        iap = get_go_bin("IAPTool")
        if not iap.exists():
            Fail("IAPTool not built")
            return 2
        key, scratch = genkey(iap)
        if len(key) != 128:
            Fail("genkey produced %d hex chars" % len(key))
            return 2
        print("  private key kept at: %s" % (scratch / "owner_key.pem"))
        # Plain ASCII: the console codepage mangles anything else, and a warning
        # that renders as mojibake is a warning nobody reads.
        print("  NOTE: from now on that key is the only one that can sign firmware")
        print("        this board will run. It is in a temp directory - move it.")
    print("  claiming with: %s..." % key[:32])

    Section("takeown")
    reply = tcp_command(ip, args.port, "takeown " + key)
    print("  reply: %s" % reply)

    Section("after")
    now = tcp_command(ip, args.port, "getpubkey")
    print("  getpubkey: %s" % now)

    Section("result")
    if args.expect_refused:
        if "Refused" in reply:
            Ok("refused, as expected")
            if now != was:
                Fail("  but the trusted key changed anyway!")
                return 1
            Ok("  and the trusted key is unchanged")
            return 0
        Fail("expected a refusal, got: %s" % reply)
        return 1

    if "OK" not in reply:
        Fail("takeown did not succeed: %s" % reply)
        Fail("(BOOT0 must have been held through the startup window of THIS boot)")
        return 1
    if now != key:
        Fail("the board reports a different key than the one claimed")
        Fail("  claimed  %s" % key)
        Fail("  reports  %s" % now)
        return 1
    Ok("claimed: the board now reports the new key as its root")
    print()
    print("Next: reset the board. The published-root warning should be gone and the")
    print("boot log should say 'claimed at generation 1'.")
    print("To undo: python3 tools/flash_bootloader.py  (erases the sector the records live in)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
