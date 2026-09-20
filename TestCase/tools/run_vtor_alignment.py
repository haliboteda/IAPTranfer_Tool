"""Measure the alignment SCB->VTOR actually enforces on this board.

Evidence for the ticket "VTOR 对齐到底要多少字节"
($PROD/maps/app-header-replaces-journal/issues/HDR-04-what-is-the-real-vtor-alignment.md).
The header placed in front of the app must be a whole alignment unit, and the
1024 that falls out of the arithmetic rests partly on core_cm7.h -- a copy of
the chip's documentation, not the chip. This asks the silicon.

    python3 tools/run_vtor_alignment.py            build, upload, judge
    python3 tools/run_vtor_alignment.py --no-build use the image already built
    python3 tools/run_vtor_alignment.py --build-only   desk half, no board

The verdict is a JUDGEMENT, not a fixed number: it reports which low bits the
hardware keeps and says whether that matches TBLOFF = bit[31:7]. A different
answer is a finding, not a failure -- the point is to learn what the part does.

WHAT THIS CANNOT SETTLE: the architectural requirement that the alignment also
be at least the vector table's length rounded up to a power of two. Violating
it is UNPREDICTABLE, so a part that happens to run proves nothing either way.
That half is a documentation question, not a measurement.

Exit code: 0 the measurement was taken and reported, 1 it could not be taken,
2 setup is missing.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import (Fail, Ok, Section, Warn, banner, cfg,  # noqa: E402
                    get_scratch_dir, open_log_ports, read_log_ports)

SKETCH = HERE.parent / "onboard" / "vtor_probe"
FQBN = ("OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen,xusb=FS,"
        "upload_method=cdcMethod,knxrole=dual_device")

# Where the app's vector table has to land. Not typed twice: the linker gets it
# from build.flash_offset, and an image whose table is elsewhere flashes and
# verifies fine, then faults on the jump -- recoverable only with BOOT0.
EXPECTED_ISR_VECTOR = 0x08020000

# TBLOFF is bit[31:7] per core_cm7.h, so bits 0..6 should come back cleared and
# bit 7 upward should stick. This is the EXPECTATION the run is judged against,
# not a pass/fail threshold.
EXPECTED_LOWEST_KEPT_BIT = 7

REPORT = re.compile(r"bit(\d+)\s*\(\+0x[0-9A-Fa-f]+\)\s*wrote 0x([0-9A-Fa-f]{8})"
                    r"\s+read 0x([0-9A-Fa-f]{8})")


def build():
    """Compile the probe and refuse to hand back an image linked to the wrong place."""
    Section("building the probe")
    cli = getattr(cfg, "ARDUINO_CLI", "")
    cli_cfg = getattr(cfg, "ARDUINO_CLI_CONFIG", "")
    if not cli or not Path(cli).exists():
        Fail("arduino-cli not found; set ARDUINO_CLI in config/machine.py")
        return None
    build_path = Path(get_scratch_dir()) / "vtor_probe_build"
    shutil.rmtree(str(build_path), ignore_errors=True)

    # --build-property REPLACES the flag list rather than appending to it, so
    # VECT_TAB_OFFSET has to be restated or the image links wrong.
    flags = "-DVECT_TAB_OFFSET={build.flash_offset}"
    argv = [cli, "compile", "--config-file", cli_cfg, "--fqbn", FQBN,
            "--build-property", "compiler.c.extra_flags=" + flags,
            "--build-property", "compiler.cpp.extra_flags=" + flags,
            "--build-path", str(build_path), str(SKETCH)]
    rc = subprocess.run(argv, capture_output=True, text=True)
    if rc.returncode != 0:
        Fail("the probe did not compile:")
        for line in rc.stdout.splitlines()[-15:]:
            print("    %s" % line)
        return None

    maps = list(build_path.glob("*.map"))
    if not maps:
        Fail("no .map in %s -- cannot confirm where the vector table landed" % build_path)
        return None
    where = None
    for line in maps[0].read_text(encoding="utf-8", errors="replace").splitlines():
        m = re.match(r"^\.isr_vector\s+0x0*([0-9a-fA-F]+)\s+0x([0-9a-fA-F]+)", line)
        if m:
            where, size = int(m.group(1), 16), int(m.group(2), 16)
            break
    if where is None:
        Fail("the map does not name .isr_vector -- refusing to upload blind")
        return None
    if where != EXPECTED_ISR_VECTOR:
        Fail("vector table at 0x%08X, expected 0x%08X" % (where, EXPECTED_ISR_VECTOR))
        Fail("   this image would flash, verify, and then fault on the jump")
        Fail("   the VECT_TAB_OFFSET build property was probably dropped")
        return None
    Ok("vector table at 0x%08X, %d bytes" % (where, size))

    bins = list(build_path.glob("*.ino.bin"))
    if not bins:
        Fail("no .bin produced")
        return None
    Ok("image: %s" % bins[0])
    return bins[0]


def judge(text):
    """Report which low bits the hardware kept, and whether that is what we expected."""
    Section("what the hardware did with the bits we set")
    hits = REPORT.findall(text)
    if not hits:
        Fail("the probe printed no bit report -- is RS232 connected, and did the app start?")
        Warn("   heartbeat line to look for: VTOR_PROBE alive")
        return 1

    kept, dropped = [], []
    for bit, wrote, read in hits:
        bit = int(bit)
        (kept if int(wrote, 16) == int(read, 16) else dropped).append(bit)
        print("  bit%-3d wrote 0x%s  read 0x%s  %s"
              % (bit, wrote, read, "kept" if int(wrote, 16) == int(read, 16) else "dropped"))

    if not kept:
        Fail("every probed bit was dropped -- widen the probe, the answer is above bit9")
        return 1
    lowest_kept = min(kept)
    enforced = 1 << lowest_kept

    Section("verdict")
    print("  dropped: %s" % (", ".join("bit%d" % b for b in sorted(dropped)) or "none"))
    print("  kept   : %s" % ", ".join("bit%d" % b for b in sorted(kept)))
    Ok("the hardware enforces %d-byte alignment (lowest implemented bit is bit%d)"
       % (enforced, lowest_kept))
    if lowest_kept == EXPECTED_LOWEST_KEPT_BIT:
        Ok("matches TBLOFF = bit[31:7] -- the CMSIS header agrees with the part")
    else:
        Warn("core_cm7.h says bit[31:%d]; this part says bit[31:%d]"
             % (EXPECTED_LOWEST_KEPT_BIT, lowest_kept))
        Warn("that disagreement is the finding -- record it in the ticket")

    banner(["the header still has to be >= the vector table rounded to a power",
            "of two (664 -> 1024). This run does NOT measure that: violating it",
            "is UNPREDICTABLE, so a board that runs proves nothing about it."])
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--build-only", action="store_true",
                    help="compile and check the link address; do not touch the board")
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--bin", default="")
    ap.add_argument("--ip", default="")
    ap.add_argument("--seconds", type=int, default=25)
    ap.add_argument("--ports", nargs="*", default=None)
    args = ap.parse_args()

    image = Path(args.bin) if args.bin else None
    if not args.no_build:
        image = build()
        if image is None:
            return 1
    if args.build_only:
        Ok("desk half done; rerun without --build-only with the board attached")
        return 0
    if image is None or not image.exists():
        Fail("no image to upload; drop --no-build or pass --bin")
        return 2

    ip = args.ip or getattr(cfg, "BOARD_IP", "")
    if not ip:
        Fail("need --ip (or BOARD_IP in config/machine.py)")
        return 2

    Section("uploading the probe")
    rc = subprocess.run([sys.executable, str(HERE / "upload_and_watch.py"),
                         "--bin", str(image), "--ip", ip], text=True)
    if rc.returncode != 0:
        Fail("the upload did not succeed; nothing to measure")
        return 1

    Section("listening for the report")
    ports = args.ports if args.ports is not None else [getattr(cfg, "LOG_PORT", "")]
    handles = open_log_ports([p for p in ports if p])
    if not handles:
        Fail("no serial port opened -- the probe reports on RS232 (PC10/PC11)")
        return 1
    text = read_log_ports(handles, args.seconds)
    return judge(text)


if __name__ == "__main__":
    sys.exit(main())
