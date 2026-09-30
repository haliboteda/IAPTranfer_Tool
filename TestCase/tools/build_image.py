"""Builds the bootloader, or the port tool image, headlessly.

    python tools/build_image.py            the bootloader
    python tools/build_image.py --porttool the port tool image
    python tools/build_image.py --both     one after the other, bootloader last

Why this exists: the port tool image differs from the bootloader by one
preprocessor symbol, and the documented way to set it was by hand in the
CubeIDE project properties. That had a failure mode nobody could design away -
forget to put it back, and the next release is a "bootloader" that never starts
the IAP server and can only be recovered with an ST-Link.

The headless builder takes the symbol on the command line instead:

    -D PORTTOOL_ENABLE=1

and does NOT write it into .cproject (verified 2026-09-08: git reports the file
unchanged after such a build). So the symbol lives exactly as long as the build
that used it, and the working tree is never in the dangerous state.

Size is checked against the linker script rather than a number typed here, for
the same reason case P9 checks paths: the number in the script is the one that
decides, and a copy of it would drift.

Exit 0 = built and fits, 1 = build failed or the image is too big, 2 = setup.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import cfg, get_cube_ide_exe, Section, Ok, Warn, Fail, read_text  # noqa: E402

PROJECT = "open_plc_cube_ide/Debug"

# The intentional marker DECISIONS.md 14 requires the tool image to carry. It
# is a warning on purpose, so it cannot be missed in a build log.
# Warnings that are a consequence of a deliberate design choice, matched on an
# exact substring so nothing else hides behind them.
KNOWN_WARNINGS = (
    # .RamFunc puts the flashboot erase routine in .data, which makes that LOAD
    # segment writable and executable. That is the point: the code has to keep
    # running while sector 0 is erased. Nothing marks RAM_D1 execute-never --
    # MPU region 0's sub-region 1 is disabled, so 0x24000000 falls back to the
    # default map, where SRAM is executable.
    # $PROD/docs/modules/M1/FLASHBOOT.md
    "LOAD segment with RWX permissions",
)

TOOL_MARKER = "PORTTOOL_ENABLE=1: this image is the hardware test tool"

# The two linker scripts. The bootloader's bounds FLASH at sector 0 because
# the application lives above it; the tool's takes the whole device because
# that does not apply to an image ST-Link writes on its own.
BOOT_LD = "STM32H743IIKX_FLASH.ld"
TOOL_LD = "STM32H743IIKX_FLASH_PORTTOOL.ld"


def flash_limit():
    """The bootloader's FLASH region, straight out of the linker script.

    Only the bootloader is judged against this. The tool image is flashed
    whole by ST-Link and never travels through IAP, so its size is a number
    to record, not a limit to pass.
    """
    ld = Path(cfg.BOOT_REPO) / "STM32H743IIKX_FLASH.ld"
    m = re.search(r"FLASH\s*\(rx\)\s*:\s*ORIGIN\s*=\s*\S+?,\s*LENGTH\s*=\s*(\d+)K",
                  read_text(ld))
    if not m:
        Fail("no FLASH length in %s" % ld)
        sys.exit(2)
    return int(m.group(1)) * 1024


def build(porttool, clean):
    what = "port tool image" if porttool else "bootloader"
    Section("Build: %s" % what)

    argv = [str(get_cube_ide_exe()), "--launcher.suppressErrors", "-nosplash",
            "-application", "org.eclipse.cdt.managedbuilder.core.headlessbuild",
            "-data", str(cfg.WORKSPACE)]
    if porttool:
        # Same principle as the macro: the tool's linker script is named only
        # in the build that asks for it, never written into .cproject. The
        # project's own default is the bootloader's script, so a plain build
        # and a build from inside the IDE both stay bootloader builds.
        argv += ["-D", "PORTTOOL_ENABLE=1",
                 "-E", "PLC_LD_SCRIPT=%s" % TOOL_LD]
    # A clean build when the symbol changes: the objects from the other flavour
    # are still on disk and make has no idea the macro moved.
    argv += ["-cleanBuild" if clean else "-build", PROJECT]

    out = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace").stdout or ""

    # A compile error stops make before "Build Finished" is ever printed, so
    # both shapes of failure have to be looked for.
    if re.search(r"(?m)^make:.*Error \d+", out) or re.search(r"(?m):\d+:\d+: error:", out):
        Fail("the build failed")
        for line in out.splitlines():
            if re.search(r"error:|Error \d+|overflowed", line):
                print("    %s" % line)
        return None

    finished = re.findall(r"Build Finished\. (\d+) errors?, (\d+) warnings?", out)
    if not finished:
        Fail("the build never reported finishing - see the log above")
        print(out[-2000:])
        return None
    errors, warnings = finished[-1]
    if errors != "0":
        Fail("%s errors" % errors)
        return None

    want_ld = TOOL_LD if porttool else BOOT_LD
    other_ld = BOOT_LD if porttool else TOOL_LD
    if ("-T" + other_ld) in out.replace('"', '') or (" " + other_ld) in out:
        Fail("the linker was given %s, but this build wanted %s - check the "
             "PLC_LD_SCRIPT default in .settings/org.eclipse.cdt.core.prefs"
             % (other_ld, want_ld))
        return None
    if want_ld in out:
        Ok("linked against %s" % want_ld)
    else:
        # Every build here is a clean build, so there is always a link. No
        # linker line naming the script means the -T never arrived - most
        # likely PLC_LD_SCRIPT expanded to nothing.
        Fail("no linker line names %s - did PLC_LD_SCRIPT expand? See "
             "$PROD/docs/build/CUBEMX-RULES.md" % want_ld)
        return None

    marker_seen = TOOL_MARKER in out
    if porttool and not marker_seen:
        # Without the marker the symbol did not reach the compiler, and what
        # was just built is a bootloader wearing the tool's name.
        Fail("this was supposed to be the tool image, but the "
             "PORTTOOL_ENABLE marker is not in the build log")
        return None
    if not porttool and marker_seen:
        Fail("this was supposed to be the bootloader, but it carries the port "
             "tool marker - check .cproject for a leftover PORTTOOL_ENABLE")
        return None

    # The tool image's one expected warning is that marker.
    expected = (1 if porttool else 0) + sum(
        1 for line in out.splitlines()
        if any(k in line for k in KNOWN_WARNINGS))
    if int(warnings) > expected:
        Warn("%s warnings (expected %d)" % (warnings, expected))
        for line in out.splitlines():
            if ("warning:" in line and TOOL_MARKER not in line
                    and not any(k in line for k in KNOWN_WARNINGS)):
                print("    %s" % line)
    else:
        Ok("0 errors, %s warning(s) - as expected" % warnings)

    binary = Path(cfg.BOOT_REPO) / "Debug" / "open_plc_cube_ide.bin"
    if not binary.exists():
        Fail("no .bin at %s" % binary)
        return None
    size = binary.stat().st_size
    if porttool:
        # No limit of its own: ST-Link writes it whole, IAP never sees it.
        Ok("%s: %d bytes (no size limit - flashed by ST-Link)" % (what, size))
        return size
    limit = flash_limit()
    if size > limit:
        Fail("%s is %d bytes, over the %d the linker script allows" %
             (binary.name, size, limit))
        return None
    Ok("%s: %d bytes, %d to spare in the %d-byte region" %
       (what, size, limit - size, limit))
    return size


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--porttool", action="store_true",
                    help="build the port tool image instead of the bootloader")
    ap.add_argument("--both", action="store_true",
                    help="build both, bootloader last so Debug/ is left safe")
    args = ap.parse_args()

    if args.both:
        if build(porttool=True, clean=True) is None:
            return 1
        # Bootloader last on purpose: whatever is left in Debug/ is what a
        # careless flash would write, and that should never be the tool image.
        if build(porttool=False, clean=True) is None:
            return 1
        Ok("Debug/ now holds the BOOTLOADER")
        return 0

    size = build(porttool=args.porttool, clean=True)
    if size is None:
        return 1
    if args.porttool:
        Warn("Debug/ now holds the PORT TOOL image, which is not a bootloader. "
             "Run this script with no arguments to put the bootloader back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
