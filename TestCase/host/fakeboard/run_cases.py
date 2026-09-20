"""Drives the real IAPTool against fake_board.py and checks the decision it
makes about who may talk to this board -- before any firmware is sent. Cases
T1-18a-T1-18g (selfcheck runs them under that id).

Why this cannot be done on a real board: the outcomes below differ only in
which key the bootloader was compiled with, and in what key and certificate
are present on the host. Reproducing them on hardware means reflashing the
bootloader with a different key for each case. Here it is a command-line
argument.

What is under test is IAPTool, not the device. fake_board.py verifies nothing;
device-side verification is covered by S1 against real hardware.

    python run_cases.py              run all cases
    python run_cases.py --keep       keep the scratch directory for inspection

Two things worth knowing:

  * "python is not on PATH" is not checked. This interpreter is what launches
    fake_board.py, so there is nothing to look up -- gating on the literal
    "python" is what would stop the suite on a python3-only machine.
  * the IAPTool copy keeps the platform's executable suffix rather than
    hardcoding ".exe".

Exit 0 = all matched, 1 = at least one did not, 2 = prerequisites missing.
"""

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (Fail, Ok, Section, boot_key_paths, build_iap_tool,  # noqa: E402
                     fixed_bytes, have_cmd, nonblank_lines,
                     parse_hex_bytes, read_text, resolve_port, run_capture,
                     stage_iap_tool, start_fake_board, stop_fake_board,
                     trusted_pubkey_hex, wait_for_listener)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if not have_cmd("go"):
        Fail("go is not on PATH")
        return 2

    iap_tool = build_iap_tool()
    port = resolve_port()

    good_hex = trusted_pubkey_hex()
    if good_hex is None:
        return 2

    _, good_key = boot_key_paths()
    if not good_key.exists():
        Fail("not found: %s" % good_key)
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="fakeboard-"))
    print("scratch: %s" % scratch)

    iap_run = stage_iap_tool(scratch, iap_tool, port)

    # A second, unrelated key pair for the mismatch cases. IAPTool can make one,
    # so nothing has to be committed and nothing depends on openssl.
    gen_out, _ = run_capture([iap_run, "genkey", "other_key"], cwd=scratch)
    bad_hex = parse_hex_bytes(gen_out)
    if len(bad_hex) != 128:
        Fail("IAPTool genkey output parsed to %d hex chars" % len(bad_hex))
        return 2

    bin_path = scratch / "app.bin"
    bin_path.write_bytes(fixed_bytes(2048, 31, 7))

    # A root of our own, and a leaf it certifies. Issuing a delegated
    # certificate advances the counter beside the issuing key, so it is done
    # with a scratch root rather than the repository's -- a host test must not
    # write into a checked-out tree.
    gen_out, _ = run_capture([iap_run, "genkey", "cert_root"], cwd=scratch)
    root_hex = parse_hex_bytes(gen_out)
    root_key = scratch / "cert_root.pem"
    gen_out, _ = run_capture([iap_run, "genkey", "leaf_key"], cwd=scratch)
    leaf_hex = parse_hex_bytes(gen_out)
    leaf_key = scratch / "leaf_key.pem"
    if len(root_hex) != 128 or len(leaf_hex) != 128:
        Fail("IAPTool genkey output did not parse to a 128-hex-char key")
        return 2

    # The certificate lives at "<the key it covers>.cert", which is where
    # IAPTool looks when no --cert is given -- the same path an Arduino install
    # would use, since the IDE passes no options at all.
    def issue_cert(leaf_pub_hex, dest):
        out, rc = run_capture([iap_run, "cert", leaf_pub_hex, "--key=%s" % root_key], cwd=scratch)
        line = next((ln.strip() for ln in out.splitlines()
                     if re.fullmatch(r"[0-9a-f]{256}", ln.strip())), None)
        if line is None:
            Fail("IAPTool cert produced no certificate (rc=%d):\n%s" % (rc, out))
            return False
        Path(dest).write_text(line + "\n", encoding="utf-8")
        return True

    if not issue_cert(leaf_hex, str(leaf_key) + ".cert"):
        return 2
    # Same root, but issued for somebody else's key: the tool must notice
    # before the board does.
    wrong_leaf_cert = scratch / "wrong_leaf.pem"
    if not issue_cert(bad_hex, str(wrong_leaf_cert) + ".cert"):
        return 2
    shutil.copy2(str(leaf_key), str(wrong_leaf_cert))

    # id, board's pubkey, --key to pass (or ""), expected line
    cases = [
        {"id": "key-match", "pub": good_hex, "key": good_key,
         "expect": "Signing key matches this board"},
        {"id": "key-mismatch", "pub": bad_hex, "key": good_key,
         "expect": "verifies against a different signing key"},
        {"id": "old-bootload", "pub": "unknown", "key": good_key,
         "expect": "skipping key match check"},
        {"id": "cert-match", "pub": root_hex, "key": leaf_key,
         "expect": "Certificate was issued by this board's root"},
        {"id": "cert-wrong-root", "pub": bad_hex, "key": leaf_key,
         "expect": "was not issued by this board's root"},
        {"id": "cert-key-mismatch", "pub": root_hex, "key": wrong_leaf_cert,
         "expect": "was issued for a different key"},
        {"id": "no-key", "pub": good_hex, "key": "",
         "expect": "no signing key found"},
    ]

    failed = 0

    for c in cases:
        Section("%s  -- expecting: %s" % (c["id"], c["expect"]))

        board, board_log, handles = start_fake_board(
            scratch, c["id"], [c["pub"], "30", "--port", port])

        if not wait_for_listener(port):
            Fail("fake board never listened on %s" % port)
            stop_fake_board(board, handles)
            if board_log.exists():
                for line in re.split(r"\r?\n", read_text(board_log)):
                    print("  %s" % line)
            failed += 1
            continue

        argv = [iap_run, "ether", bin_path, "127.0.0.1"]
        if c["key"]:
            argv.append("--key=%s" % c["key"])
        out, _ = run_capture(argv)

        # log= so the process is provably gone before the next case binds the
        # same port -- see stop_fake_board().
        stop_fake_board(board, handles, log=board_log)

        # Deliberately case-insensitive.
        if c["expect"].lower() in out.lower():
            Ok("PASS")
        else:
            Fail("FAIL - expected line not found. IAPTool said:")
            for line in nonblank_lines(out):
                print("    %s" % line)
            failed += 1

    if not args.keep:
        shutil.rmtree(str(scratch), ignore_errors=True)
    else:
        print("kept: %s" % scratch)

    Section("result")
    if failed > 0:
        Fail("%d of %d case(s) failed" % (failed, len(cases)))
        return 1
    Ok("all %d key-match cases behaved as expected" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
