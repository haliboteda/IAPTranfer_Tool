"""Cross-checks the product's crypto against independent implementations.

  1. sha256_ref.py   the bootloader's SHA-256/HMAC construction vs hashlib
  2. ecdsa_verify.py signatures IAPTool actually produced, verified by hand-
                     rolled modular arithmetic that shares no code with Go

Signing is randomised, so step 2 signs the same blob several times and verifies
every result. One passing signature proves the encoding round-trips; several
prove it does not depend on a lucky value of r or s (a leading zero byte in
either integer is the classic case, and it shows up roughly once in 256
signatures -- rare enough to reach the field, common enough to be certain it
eventually will).

    python run_checks.py                default 12 signatures
    python run_checks.py --rounds 64    more, when key or signer code changed

The Python side of M7 step 3, and a drop-in for run-checks.ps1 (whose switch is
spelled -Rounds).

One deliberate difference: the PowerShell version starts by refusing to run if
"python" is not on PATH. There is nothing to check here -- this interpreter is
already running, and it is the one used to launch both reference scripts. On a
machine carrying python3 but no python the PowerShell version stops at that gate
while this one works, which is the whole point of M7. Neither prints anything on
a machine that has both, so the comparison is unaffected.

Exit 0 = everything verified, 1 = something did not, 2 = prerequisites missing.
"""

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import (EXE, GOOS_DIR, Fail, Ok, Section, Warn, cfg,  # noqa: E402
                    get_go_bin, have_cmd, python_exe, read_text, run_emit)


def parse_pubkey_inc(path):
    """The 0xNN byte array the bootloader #includes, as lower-case hex.

    Parsed rather than copied so this cannot go stale the first time anyone
    rotates keys.
    """
    return "".join(re.findall(r"0x([0-9a-fA-F]{2})", read_text(path))).lower()


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--rounds", type=int, default=12)
    args = ap.parse_args()
    rounds = args.rounds

    bad = 0

    Section("1. SHA-256 / HMAC-SHA-256 construction")
    if run_emit([python_exe(), HERE / "sha256_ref.py"]) != 0:
        Fail("SHA-256 reference check failed")
        bad += 1
    else:
        Ok("PASS")

    Section("2. ECDSA P-256 signatures from IAPTool")

    if not have_cmd("go"):
        Warn("SKIP - go is not on PATH, cannot produce signatures")
    else:
        iap_tool = get_go_bin("IAPTool")
        if not iap_tool.exists():
            run_emit(["go", "build", "-o", "Output/%s/IAPTool%s" % (GOOS_DIR, EXE), "."],
                     cwd=cfg.TOOL_REPO)

        key = Path(cfg.BOOT_REPO) / "IAPServer/keys/fw_signing_key.TEST_ONLY.pem"
        inc = Path(cfg.BOOT_REPO) / "IAPServer/keys/fw_pubkey.inc"
        for p in (iap_tool, key, inc):
            if not Path(p).exists():
                Fail("not found: %s" % p)
                return 2

        # The public key comes from the same .inc the bootloader compiles in, so
        # this checks the committed key pair, not an ad-hoc one.
        pub_hex = parse_pubkey_inc(inc)
        if len(pub_hex) != 128:
            Fail("fw_pubkey.inc parsed to %d hex chars" % len(pub_hex))
            return 2
        print("  key pair: IAPServer/keys/fw_signing_key.TEST_ONLY.pem -> %s..."
              % pub_hex[:16])

        scratch = Path(tempfile.mkdtemp(prefix="cryptoref-"))

        msg = scratch / "msg.bin"
        msg.write_bytes(bytes((i * 17 + 3) % 256 for i in range(4096)))

        verified = 0
        for n in range(1, rounds + 1):
            prefix = scratch / ("run%d" % n)
            run_capture_quiet([iap_tool, "sign", msg, key, "--out=%s" % prefix])
            sig = Path(str(prefix) + ".sig")
            if not sig.exists():
                Fail("round %d: IAPTool produced no .sig" % n)
                bad += 1
                continue

            print("  round %d" % n)
            if run_emit([python_exe(), HERE / "ecdsa_verify.py", pub_hex, msg, sig]) != 0:
                Fail("round %d: independent verification FAILED" % n)
                bad += 1
            else:
                verified += 1

        shutil.rmtree(str(scratch), ignore_errors=True)

        if verified == rounds:
            Ok("PASS - %d/%d signatures verified independently" % (verified, rounds))
        else:
            Fail("only %d of %d verified" % (verified, rounds))

    Section("result")
    if bad > 0:
        Fail("%d check(s) failed" % bad)
        return 1
    Ok("the shipping signer and an independent verifier agree")
    return 0


def run_capture_quiet(argv):
    """`... 2>&1 | Out-Null` -- run it, throw the output away."""
    import subprocess
    subprocess.run([str(a) for a in argv],
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


if __name__ == "__main__":
    sys.exit(main())
