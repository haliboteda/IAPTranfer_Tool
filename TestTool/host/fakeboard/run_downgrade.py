"""DG1 -- the downgrade guard. Drives the real IAPTool against fake_board.py and
checks what it does when the image is older than what the device reports.
Case DG1 (selfcheck runs it under that id).

Covers requirement C6 on the host side. Until this existed, --downgrade had no
test at all: the only evidence it worked was that somebody once watched it print
the word "refused".

Two assertions per case, and the second is the point:

  1. what IAPTool said on stdout, and
  2. whether the stand-in board was ever sent a "flash" command.

A tool that printed "Downgrade refused" and then uploaded anyway would sail
through a log-only check. C6's actual claim is that the installed app is not
touched, so the board's own log is what has to prove it.

Why no real board is needed: every branch under test lives in IAPTool
(auth.go confirmDowngradeIfNeeded). The device contributes exactly one thing, its
answer to "getversion", which fake_board.py serves from --fwver. Doing this on
hardware would mean flashing a real older app to set the board up for each case.
Device-side behaviour is S1/S2/G1 against hardware.

⚠️ This covers the TOOL only. The bootloader parses the version out of the flash
command and never compares it (IAPServer/IAP_server.c:332-357), so a modified
tool can still downgrade a board. That gap is case DG2, which talks to the device
directly.

    python run_downgrade.py              run all five
    python run_downgrade.py --keep       keep the scratch directory

The Python side of M7 step 3, and a drop-in for run-downgrade.ps1 (whose switch
is spelled -Keep). Same three deliberate differences as run_cases.py.

Exit 0 = all five matched, 1 = at least one did not, 2 = prerequisites missing.
"""

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (Fail, Ok, Section, boot_key_paths, build_iap_tool,  # noqa: E402
                     encode_version, fixed_bytes, have_cmd, nonblank_lines,
                     read_text, resolve_port, run_capture, stage_iap_tool,
                     start_fake_board, stop_fake_board, trusted_pubkey_hex,
                     wait_for_listener)


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if not have_cmd("go"):
        Fail("go is not on PATH")
        return 2

    iap_tool = build_iap_tool()
    port = resolve_port()

    # The board has to answer getpubkey with the key IAPTool signs with: the
    # version query happens on the same preflight connection, *after* the key
    # match check, and a mismatch aborts before getversion is ever sent.
    good_hex = trusted_pubkey_hex()
    if good_hex is None:
        return 2

    _, good_key = boot_key_paths()
    if not good_key.exists():
        Fail("not found: %s" % good_key)
        return 2

    scratch = Path(tempfile.mkdtemp(prefix="downgrade-"))
    print("scratch: %s" % scratch)

    iap_run = stage_iap_tool(scratch, iap_tool, port)

    v_older = encode_version(iap_run, "0.9.0")
    v_same = encode_version(iap_run, "1.0.0")
    v_newer = encode_version(iap_run, "1.0.1")
    if None in (v_older, v_same, v_newer):
        return 2
    print("versions: older=%s  installed=%s  newer=%s" % (v_older, v_same, v_newer))
    if int(v_older) >= int(v_same) or int(v_newer) <= int(v_same):
        Fail("version encoding does not order as expected -- the cases below "
             "would prove nothing")
        return 2

    bin_path = scratch / "app.bin"
    bin_path.write_bytes(fixed_bytes(2048, 17, 3))

    # flash = did the board see a "flash" command. False means the upload never
    # started, which is what "the installed app is untouched" reduces to here.
    #  no_console: hand IAPTool a closed stdin so "ask" hits its no-terminal
    #  branch deterministically, instead of depending on how this was launched.
    cases = [
        {"id": "refuse-older", "ver": v_older, "mode": "refuse", "no_console": False,
         "flash": False, "expect": "Downgrade refused by --downgrade=refuse."},
        {"id": "allow-older", "ver": v_older, "mode": "allow", "no_console": False,
         "flash": True, "expect": "Downgrade allowed by --downgrade=allow."},
        {"id": "ask-no-console", "ver": v_older, "mode": "ask", "no_console": True,
         "flash": False, "expect": "Cannot ask: no interactive terminal"},
        # The two reverse cases. "refuse" must not block anything that is not a
        # downgrade -- and same-version is the boundary the >= comparison turns
        # on, so an off-by-one there would lock out every re-flash of the same
        # build.
        {"id": "same-version", "ver": v_same, "mode": "refuse", "no_console": False,
         "flash": True, "expect": "File transfer complete."},
        {"id": "newer", "ver": v_newer, "mode": "refuse", "no_console": False,
         "flash": True, "expect": "File transfer complete."},
    ]

    failed = 0

    for c in cases:
        Section("%s  -- expecting: %s" % (c["id"], c["expect"]))

        board, board_log, handles = start_fake_board(
            scratch, c["id"], [good_hex, "30", "--port", port, "--fwver", v_same])

        if not wait_for_listener(port):
            Fail("fake board never listened on %s" % port)
            stop_fake_board(board, handles)
            if board_log.exists():
                for line in nonblank_lines(read_text(board_log)):
                    print("  %s" % line)
            failed += 1
            continue

        argv = [iap_run, "ether", bin_path, "127.0.0.1", "--key=%s" % good_key,
                "--version=%s" % c["ver"], "--downgrade=%s" % c["mode"]]
        out, _ = run_capture(argv, empty_stdin=c["no_console"])

        # Settle the log before reading it, and be sure the process is gone
        # before the next case binds the same port -- see stop_fake_board().
        stop_fake_board(board, handles, log=board_log)

        board_saw = read_text(board_log) if board_log.exists() else ""
        flashed = "CMD 'flash" in board_saw

        why = []
        # PowerShell's -match is case-insensitive, so these comparisons are too.
        if c["expect"].lower() not in out.lower():
            why.append("IAPTool never said: %s" % c["expect"])
        if flashed != c["flash"]:
            if c["flash"]:
                why.append("board was never sent a flash command, but this "
                           "upload should have gone through")
            else:
                why.append("board WAS sent a flash command -- the installed app "
                           "would have been overwritten")
        # The positive cases only count if the image actually landed; "flash" was
        # sent tells us the guard let go, not that the transfer survived.
        if c["flash"] and "image fully received" not in board_saw.lower():
            why.append("board never reported the image fully received")

        if not why:
            Ok("PASS")
        else:
            Fail("FAIL")
            for w in why:
                print("    %s" % w)
            print("  -- IAPTool said:")
            for line in nonblank_lines(out):
                print("    %s" % line)
            print("  -- board saw:")
            for line in nonblank_lines(board_saw):
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
    Ok("all %d downgrade cases behaved as expected" % len(cases))
    return 0


if __name__ == "__main__":
    sys.exit(main())
