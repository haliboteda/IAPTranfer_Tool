"""Drives the real IAPTool against fake_board.py and checks the decision it
makes about the firmware signing key -- before any firmware is sent. Cases
K1-K6 (selfcheck runs them under that id).

Why this cannot be done on a real board: the six outcomes below differ only in
which key the bootloader was compiled with, and in whether a private key or a
.sig file is present on the host. Reproducing them on hardware means reflashing
the bootloader with a different key for each case. Here it is a command-line
argument.

What is under test is IAPTool, not the device. fake_board.py verifies nothing;
device-side verification is covered by S1 against real hardware.

    python run_cases.py              run all six
    python run_cases.py --keep       keep the scratch directory for inspection

Two things worth knowing:

  * "python is not on PATH" is not checked. This interpreter is what launches
    fake_board.py, so there is nothing to look up -- gating on the literal
    "python" is what would stop the suite on a python3-only machine.
  * the IAPTool copy keeps the platform's executable suffix rather than
    hardcoding ".exe".

Exit 0 = all six matched, 1 = at least one did not, 2 = prerequisites missing.
"""

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (Fail, Ok, Section, boot_key_paths, build_iap_tool,  # noqa: E402
                     encode_version, fixed_bytes, have_cmd, nonblank_lines,
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

    # The board answers getversion with "3", so an unversioned upload would stop
    # to ask about a downgrade and hang with no console to answer on.
    ver_num = encode_version(iap_run, "9.9.9")
    if ver_num is None:
        return 2

    # id, board's pubkey, --key to pass (or ""), whether a .sig should exist,
    # expected line
    cases = [
        {"id": "key-match",    "pub": good_hex,  "key": good_key, "sig": False,
         "expect": "Signing key matches this board"},
        {"id": "key-mismatch", "pub": bad_hex,   "key": good_key, "sig": False,
         "expect": "verifies against a different signing key"},
        {"id": "old-bootload", "pub": "unknown", "key": good_key, "sig": False,
         "expect": "skipping key match check"},
        {"id": "sig-match",    "pub": good_hex,  "key": "",       "sig": True,
         "expect": "Signature verifies against this board"},
        {"id": "sig-mismatch", "pub": bad_hex,   "key": "",       "sig": True,
         "expect": "does not verify against this board"},
        {"id": "nothing",      "pub": good_hex,  "key": "",       "sig": False,
         "expect": "no signing key found and no signature"},
    ]

    sig_path = bin_path.with_suffix(".sig")
    failed = 0

    for c in cases:
        Section("%s  -- expecting: %s" % (c["id"], c["expect"]))

        # A .sig produced by the good key: the sig-mismatch case is "the host has
        # a valid signature, but this board trusts someone else", which is a
        # different failure from a corrupt signature.
        if sig_path.exists():
            sig_path.unlink()
        if c["sig"]:
            run_capture([iap_run, "sign", bin_path, good_key, "--version=%s" % ver_num])
            if not sig_path.exists():
                Fail("could not produce a .sig")
                failed += 1
                continue

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

        argv = [iap_run, "ether", bin_path, "127.0.0.1",
                "--version=%s" % ver_num, "--downgrade=allow"]
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
