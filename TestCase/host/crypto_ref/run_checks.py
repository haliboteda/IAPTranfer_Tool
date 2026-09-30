"""Cross-checks the product's crypto against independent implementations.

  1. sha256_ref.py   the bootloader's SHA-256 construction vs hashlib
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

No interpreter check up front: this one is already running, and it is the one
used to launch both reference scripts.

Exit 0 = everything verified, 1 = something did not, 2 = prerequisites missing.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import (EXE, GOOS_DIR, Fail, Ok, Section, Warn, cfg,  # noqa: E402
                    get_go_bin, have_cmd, python_exe, run_capture, run_emit)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--rounds", type=int, default=12)
    args = ap.parse_args()
    rounds = args.rounds

    bad = 0

    Section("1. SHA-256 construction")
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

        if not Path(iap_tool).exists():
            Fail("not found: %s" % iap_tool)
            return 2

        # A throwaway key pair from the shipping tool: no key is committed any
        # more (decision 72), and the public half comes from the tool's own
        # "pubkey", the form a board answers to getpubkey.
        scratch = Path(tempfile.mkdtemp(prefix="cryptoref-"))
        key = scratch / "k.pem"
        run_capture([iap_tool, "genkey", scratch / "k"])
        out, rc = run_capture([iap_tool, "pubkey", key])
        pub_hex = out.strip().splitlines()[-1].strip().lower() if out.strip() else ""
        if (rc != 0) or (len(pub_hex) != 128):
            Fail("IAPTool genkey/pubkey gave no 128-hex-char public key: %r" % out)
            return 2
        print("  key pair: fresh from IAPTool genkey -> %s..." % pub_hex[:16])

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
