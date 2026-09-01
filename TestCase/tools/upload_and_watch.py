"""Run a real upload through IAPTool while capturing the board's serial log, then
judge it against what SDRAM staging is supposed to do.

    python3 tools/upload_and_watch.py --bin <file.bin>              over ethernet, IP from config
    python3 tools/upload_and_watch.py --bin <file.bin> --ip 1.2.3.4
    python3 tools/upload_and_watch.py --bin <file.bin> --cdc COM6   over USB CDC
    python3 tools/upload_and_watch.py --bin <file.bin> --downgrade allow

The upload is driven by the shipping IAPTool, not by a reimplementation here:
what gets exercised has to be the code path customers use.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Warn, Fail, close_ports,  # noqa: E402
                    emit_file, get_iap_tool, get_scratch_file, open_log_ports,
                    run_while_draining)

TAIL_S = 6


def verdict(all_text, exit_code):
    Section("Verdict")
    if "SDRAM staging buffer OK" in all_text:
        Ok("  self-test: staging buffer usable")
    elif "SDRAM SELF-TEST FAILED" in all_text:
        Fail("  self-test: FAILED")
    else:
        Warn("  self-test line not seen (did the board stay in the bootloader?)")

    if "Staging in SDRAM" in all_text:
        Ok("  staged instead of erasing up front")
    else:
        Warn("  no 'Staging in SDRAM' - is this the new bootloader?")

    # The whole point of staging: the erase must come after verification.
    i_erase = all_text.find("Erasing application region")
    i_verif = all_text.find("Transfer complete, verifying")
    if i_erase >= 0 and i_verif >= 0 and i_erase > i_verif:
        Ok("  erase happened AFTER verification - this is the change working")
    elif i_erase >= 0 and i_verif < 0:
        Warn("  erased without a verification line before it")
    elif i_erase < 0:
        Warn("  no erase line - upload did not reach the commit step")

    if exit_code == 0:
        Ok("  IAPTool exit 0")
    else:
        Fail("  IAPTool exit %d" % exit_code)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bin", required=True)
    ap.add_argument("--ip", default="")
    ap.add_argument("--cdc", default="")
    ap.add_argument("--downgrade", default="allow")
    ap.add_argument("--tail-seconds", type=int, default=TAIL_S)
    ap.add_argument("--ports", nargs="*", default=None)
    args = ap.parse_args()

    ports = list(args.ports if args.ports is not None else cfg.LOG_PORTS)
    ip = args.ip
    if not ip and not args.cdc:
        ip = getattr(cfg, "BOARD_IP", "")
    if not ip and not args.cdc:
        Fail("need --ip or --cdc (or set BOARD_IP / CDC_PORT in config)")
        return 1
    image = Path(args.bin)
    if not image.exists():
        Fail("no such image: %s" % image)
        return 1
    iaptool = get_iap_tool()

    # The CDC port is the board talking to us; it cannot also be a passive log port.
    if args.cdc:
        ports = [p for p in ports if p != args.cdc]

    print("image: %s  (%s B)" % (image, format(image.stat().st_size, ",d")))

    Section("Log ports")
    open_ports = open_log_ports(ports)

    Section("Upload")
    argv = ["cdc", str(image), args.cdc] if args.cdc else ["ether", str(image), ip]
    argv.append("--downgrade=%s" % args.downgrade)
    print("IAPTool %s" % " ".join(argv))

    # The board reboots into the application after a good upload, so keep
    # listening past IAPTool's exit.
    rc, buf = run_while_draining([iaptool] + argv, open_ports,
                                 get_scratch_file("iaptool.out"),
                                 get_scratch_file("iaptool.err"),
                                 tail_seconds=args.tail_seconds)
    close_ports(open_ports)

    Section("IAPTool output (exit %d)" % rc)
    emit_file(get_scratch_file("iaptool.out"))
    emit_file(get_scratch_file("iaptool.err"))

    for k, text in buf.items():
        Section("%s  (%d bytes)" % (k, len(text)))
        if text:
            print(text)

    verdict("\n".join(buf.values()), rc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
