"""OW2 -- hand a claimed board to a new owner (requirement C10, M1 step 5).

    python3 tools/run_setowner.py --current-key <owner.pem>                 new key generated
    python3 tools/run_setowner.py --current-key <owner.pem> --bad-signature must be refused

Unlike takeown this needs NOBODY at the board: the current owner's signature is
the authorisation, and handing a board over remotely is a supported case.
Physical presence gates only the operations with no signature to check -- the
first claim, and factory reset.

What gets signed is the first 76 bytes of the record about to be written:

    type 'O' | slots 5 | format_ver 1 | generation | flags | new public key
       1          1          2 (LE)      4 (LE)     4 (LE)      64

The generation is inside the signature on purpose: without it a captured record
could be replayed into a later slot and undo a subsequent handover.

Exit 0 = ended up in the expected state, 1 = did not, 2 = setup problem.
"""

import argparse
import re
import struct
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Warn, Fail, get_go_bin,  # noqa: E402
                    run_capture, tcp_command)


def signed_prefix(generation, new_key_hex):
    """The record's first 76 bytes, laid out exactly as the bootloader reads them."""
    return (bytes([0x4F, 5])                      # type 'O', slots
            + struct.pack("<H", 1)                # format_ver
            + struct.pack("<I", generation)
            + struct.pack("<I", 0)                # flags
            + bytes.fromhex(new_key_hex))         # root_pubkey, 64 B


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--current-key", required=True)
    ap.add_argument("--ip", default="")
    ap.add_argument("--port", default="56865")
    ap.add_argument("--new-key", default="")
    ap.add_argument("--bad-signature", action="store_true")
    args = ap.parse_args()

    ip = args.ip or getattr(cfg, "BOARD_IP", "")
    if not ip:
        Fail("need --ip (or set BOARD_IP in config/machine.py)")
        return 2
    current = Path(args.current_key)
    if not current.exists():
        Fail("no such key: %s" % current)
        return 2

    iap = get_go_bin("IAPTool")
    if not iap.exists():
        Fail("IAPTool not built")
        return 2

    Section("before")
    gen = tcp_command(ip, args.port, "getowner")
    was = tcp_command(ip, args.port, "getpubkey")
    print("  generation: %s" % gen)
    print("  root:       %s" % was)
    if not re.fullmatch(r"\d+", gen):
        Fail("getowner did not answer with a number: %s" % gen)
        return 2
    if int(gen) == 0:
        Fail("the board is unclaimed - setowner needs an existing owner. Use run_takeown.py")
        return 2
    nxt = int(gen) + 1

    new_key = args.new_key
    if not new_key:
        Section("generating the incoming owner's key")
        scratch = Path(tempfile.gettempdir()) / ("setowner-" + uuid.uuid4().hex[:8])
        scratch.mkdir(parents=True, exist_ok=True)
        out, _ = run_capture([iap, "genkey", "new_owner"], cwd=scratch)
        new_key = "".join(re.findall(r"0x([0-9a-fA-F]{2})", out)).lower()
        if len(new_key) != 128:
            Fail("genkey produced %d hex chars" % len(new_key))
            return 2
        print("  private key: %s" % (scratch / "new_owner.pem"))

    Section("signed prefix")
    prefix_hex = signed_prefix(nxt, new_key).hex()
    print("  generation %d, 76 bytes" % nxt)

    sig, _ = run_capture([iap, "signraw", prefix_hex, str(current)])
    sig = sig.strip()
    if len(sig) != 128:
        Fail("signraw returned %d chars: %s" % (len(sig), sig))
        return 2

    if args.bad_signature:
        # Flip one bit of a signature that is otherwise perfectly formed. A wrong
        # signature has to be rejected for the same reason a missing one is --
        # and this is the case a "does it write the record" test would sail past.
        sig = "%02x" % (int(sig[:2], 16) ^ 0x01) + sig[2:]
        Warn("  signature deliberately corrupted")

    Section("setowner")
    reply = tcp_command(ip, args.port, "setowner %d %s %s" % (nxt, new_key, sig))
    print("  reply: %s" % reply)

    Section("after")
    now_gen = tcp_command(ip, args.port, "getowner")
    now = tcp_command(ip, args.port, "getpubkey")
    print("  generation: %s" % now_gen)
    print("  root:       %s" % now)

    Section("result")
    if args.bad_signature:
        if "Refused" not in reply:
            Fail("expected a refusal, got: %s" % reply)
            return 1
        if now != was:
            Fail("refused, but the root changed anyway!")
            return 1
        if now_gen != gen:
            Fail("refused, but the generation moved!")
            return 1
        Ok("refused, and nothing changed")
        return 0

    if "OK" not in reply:
        Fail("setowner did not succeed: %s" % reply)
        return 1
    if now != new_key:
        Fail("the board reports a different root than the one handed over")
        return 1
    if now_gen != str(nxt):
        Fail("generation is %s, expected %d" % (now_gen, nxt))
        return 1
    Ok("handed over: generation %d, and the board reports the new root" % nxt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
