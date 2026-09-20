"""OW1 -- claim a board for a signing key, and check it took (requirement R2-02).

    python3 tools/run_takeown.py                    claim with a freshly generated key
    python3 tools/run_takeown.py --key owner.pem    claim with a specific key
    python3 tools/run_takeown.py --expect-refused   the board should say no (negative case)

The claim is driven through the shipping tool -- `IAPTool takeown` -- because
that is the path a customer has. The check afterwards is NOT: it asks the board
directly over TCP, so the tool cannot be the one confirming its own work.

⚠️ THIS NEEDS SOMEBODY AT THE BOARD, and that is the whole point. takeown is
gated on BOOT0 having been held through the startup window: the first claim
carries no signature -- there is no owner yet to sign it -- so physical presence
is the only gate there can be. See $PROD/docs/modules/M2-ownership.md.

Before running: press RESET, then hold BOOT0 until the relays finish clicking
and let go. The board should be sitting in "UPLOAD Mod ... (BOOT0 held)".

⚠️ RECOVERY: claiming is meant to be hard to undo. The only way back is to
reflash the bootloader over ST-Link -- the owner records live in the
bootloader's own sector, so erasing it to write the bootloader takes them with
it, and the application that was rejected while the board was claimed starts
again by itself:

    python3 tools/flash_bootloader.py

Exit 0 = the board ended up in the expected state, 1 = it did not, 2 = setup.
"""

import argparse
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Fail, banner,  # noqa: E402
                    get_go_bin, run_capture, tcp_command)


def genkey(iap):
    """Run IAPTool genkey in a fresh directory and return the .pem path.

    NOT a temp directory: after the claim this key is the only one that can
    sign firmware for this board, and a cleaned temp directory costs a
    bootloader reflash. Output/ is gitignored, so the key does not reach git
    either. Criterion T2-01, $PROD/docs/modules/M2-ownership.md.
    """
    where = (Path(cfg.TOOL_REPO) / "Output" / "owner-keys" /
             datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    where.mkdir(parents=True, exist_ok=True)
    run_capture([iap, "genkey", "owner_key"], cwd=where)
    return where / "owner_key.pem"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ip", default="")
    ap.add_argument("--port", default="56865")
    ap.add_argument("--key", default="", help="the owner's private key (PEM)")
    ap.add_argument("--expect-refused", action="store_true")
    args = ap.parse_args()

    ip = args.ip or getattr(cfg, "BOARD_IP", "")
    if not ip:
        Fail("need --ip (or set BOARD_IP in config/machine.py)")
        return 2

    iap = get_go_bin("IAPTool")
    if not iap.exists():
        Fail("IAPTool not built")
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
    # The generation BEFORE, because the claim is judged on a delta, not on a
    # fixed number: owner_slot_claim() writes "past everything already
    # written", so a board that was factory-reset comes back at 6, not at 1.
    was_gen = tcp_command(ip, args.port, "getowner")
    print("  getpubkey: %s" % was)
    print("  getowner:  %s" % was_gen)
    if not re.fullmatch(r"[0-9a-fA-F]{128}", was):
        Fail("the board did not answer getpubkey with a key -- is it in the bootloader?")
        return 2

    key = Path(args.key) if args.key else None
    if key is None:
        Section("generating a key to claim with")
        key = genkey(iap)
        # Plain ASCII: the console codepage mangles anything else, and a warning
        # that renders as mojibake is a warning nobody reads.
        print("  private key kept at: %s" % key)
        print("  NOTE: from now on that key is the only one that can sign firmware")
        print("        this board will run. Back it up - losing it costs a")
        print("        bootloader reflash (tools/flash_bootloader.py) and a new claim.")
    if not key.exists():
        Fail("no such key: %s" % key)
        return 2

    Section("takeown  (through IAPTool, the way a customer does it)")
    out, rc = run_capture([iap, "takeown", ip, "--key=%s" % key])
    print(out.strip())

    claimed = ""
    m = re.search(r"^\s*([0-9a-fA-F]{128})\s*$", out, re.M)
    if m:
        claimed = m.group(1).lower()

    Section("after  (asked of the board, not of the tool)")
    now = tcp_command(ip, args.port, "getpubkey")
    gen = tcp_command(ip, args.port, "getowner")
    print("  getpubkey: %s" % now)
    print("  getowner:  %s" % gen)

    Section("result")
    if args.expect_refused:
        if rc == 0:
            Fail("expected a refusal, but IAPTool reported success")
            return 1
        if "refused" not in out.lower():
            Fail("IAPTool failed for some other reason:\n%s" % out.strip())
            return 1
        Ok("refused, as expected")
        if now != was:
            Fail("  but the trusted key changed anyway!")
            return 1
        Ok("  and the trusted key is unchanged")
        return 0

    if rc != 0:
        Fail("takeown did not succeed:\n%s" % out.strip())
        Fail("(BOOT0 must have been held through the startup window of THIS boot)")
        return 1
    if not claimed:
        Fail("could not tell from IAPTool's output which key it claimed with")
        return 1
    if now.lower() != claimed:
        Fail("the board reports a different key than the one claimed")
        Fail("  claimed  %s" % claimed)
        Fail("  reports  %s" % now)
        return 1
    try:
        want = int(was_gen.strip()) + 1
        got = int(gen.strip())
    except ValueError:
        Fail("cannot read the generation: before=%r after=%r" % (was_gen, gen))
        return 1
    if got != want:
        Fail("the board reports generation %d; it was %s before, so %d was due"
             % (got, was_gen.strip(), want))
        return 1
    Ok("claimed: the board now reports the new key as its root, at generation %d" % got)
    print()
    print("Next: reset the board. The published-root warning should be gone, the boot")
    print("log should say 'claimed at generation %d', and an application signed by the" % got)
    print("OLD key must now be refused - that is what proves the new key is in use.")
    print("To undo: python3 tools/flash_bootloader.py  (erases the sector the records live in)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
