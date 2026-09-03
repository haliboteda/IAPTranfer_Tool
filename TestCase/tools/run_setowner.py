"""OW2 -- hand a claimed board to a new owner (requirement C10, M1 step 5).

    python3 tools/run_setowner.py --current-key <owner.pem>                 new key generated
    python3 tools/run_setowner.py --current-key <owner.pem> --new-key <next.pem>
    python3 tools/run_setowner.py --current-key <owner.pem> --bad-signature must be refused

The handover is driven through the shipping tool -- `IAPTool setowner` -- so
what gets tested is the path a customer has. The state afterwards is read from
the board over TCP, not from the tool.

⚠️ `--bad-signature` is the one branch that stays hand-rolled. A correct tool
cannot produce a wrong signature, and that case is about the BOARD's behaviour:
a well-formed record whose signature is off by one bit has to be refused for the
same reason a missing one is. It builds the record and signs it here, then
corrupts the signature.

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


def genkey(iap, label):
    """Run IAPTool genkey in a fresh directory. Returns (pem path, pubkey hex)."""
    scratch = Path(tempfile.gettempdir()) / (label + "-" + uuid.uuid4().hex[:8])
    scratch.mkdir(parents=True, exist_ok=True)
    out, _ = run_capture([iap, "genkey", "new_owner"], cwd=scratch)
    pub = "".join(re.findall(r"0x([0-9a-fA-F]{2})", out)).lower()
    return scratch / "new_owner.pem", pub


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--current-key", required=True)
    ap.add_argument("--ip", default="")
    ap.add_argument("--port", default="56865")
    ap.add_argument("--new-key", default="", help="the incoming owner's private key (PEM)")
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

    new_pem, new_pub = "", ""
    if args.new_key:
        new_pem = Path(args.new_key)
        if not new_pem.exists():
            Fail("no such key: %s" % new_pem)
            return 2
    else:
        Section("generating the incoming owner's key")
        new_pem, new_pub = genkey(iap, "setowner")
        if len(new_pub) != 128:
            Fail("genkey produced %d hex chars" % len(new_pub))
            return 2
        print("  private key: %s" % new_pem)

    if args.bad_signature:
        # A correct tool cannot produce this, so the record is built and signed
        # here and then broken on purpose. What is under test is the board.
        if not new_pub:
            Fail("--bad-signature generates its own key; do not pass --new-key")
            return 2
        Section("signed prefix, then corrupted")
        prefix_hex = signed_prefix(nxt, new_pub).hex()
        sig, _ = run_capture([iap, "signraw", prefix_hex, str(current)])
        sig = sig.strip()
        if len(sig) != 128:
            Fail("signraw returned %d chars: %s" % (len(sig), sig))
            return 2
        # Flip one bit of a signature that is otherwise perfectly formed. A wrong
        # signature has to be rejected for the same reason a missing one is --
        # and this is the case a "does it write the record" test would sail past.
        sig = "%02x" % (int(sig[:2], 16) ^ 0x01) + sig[2:]
        Warn("  signature deliberately corrupted")
        reply = tcp_command(ip, args.port, "setowner %d %s %s" % (nxt, new_pub, sig))
        print("  reply: %s" % reply)
        rc = 0 if "OK" in reply else 1
        out = reply
    else:
        Section("setowner  (through IAPTool, the way a customer does it)")
        out, rc = run_capture([iap, "setowner", ip,
                               "--current-key=%s" % current,
                               "--new-key=%s" % new_pem])
        print(out.strip())
        m = re.search(r"new key:\s*([0-9a-fA-F]{128})", out)
        if m:
            new_pub = m.group(1).lower()

    Section("after  (asked of the board, not of the tool)")
    now_gen = tcp_command(ip, args.port, "getowner")
    now = tcp_command(ip, args.port, "getpubkey")
    print("  generation: %s" % now_gen)
    print("  root:       %s" % now)

    Section("result")
    if args.bad_signature:
        if rc == 0:
            Fail("expected a refusal, got: %s" % out)
            return 1
        if now != was:
            Fail("refused, but the root changed anyway!")
            return 1
        if now_gen != gen:
            Fail("refused, but the generation moved!")
            return 1
        Ok("refused, and nothing changed")
        return 0

    if rc != 0:
        Fail("setowner did not succeed:\n%s" % out.strip())
        return 1
    if not new_pub:
        Fail("could not tell from IAPTool's output which key it handed over to")
        return 1
    if now.lower() != new_pub:
        Fail("the board reports a different root than the one handed over")
        Fail("  handed to %s" % new_pub)
        Fail("  reports   %s" % now)
        return 1
    if now_gen != str(nxt):
        Fail("generation is %s, expected %d" % (now_gen, nxt))
        return 1
    Ok("handed over: generation %d, and the board reports the new root" % nxt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
