"""The application's start address must be a multiple of 1024. Case P15.

Evidence for R1-33. Criterion: $PROD/docs/engineering/HOW-TO-RUN-TESTS.md.

The app's vector table sits at the start of FLASH, so VTOR's alignment
requirement lands on LD_FLASH_OFFSET -- and nothing used to enforce it.
ldscript.ld gives .isr_vector only ALIGN(4), so a wrong offset links, flashes
and verifies before faulting on the jump, which only BOOT0 recovers. An ASSERT
in the linker script now refuses it, and this is what proves the ASSERT is
still armed.

Two builds, and the SECOND one is the point:

  positive  the offset the board actually ships with  -> must link
  negative  0x20200, deliberately not 1024-aligned    -> must NOT link,
            and the error must name the alignment

Without the negative build a broken ASSERT -- deleted, or written so it can
never be false -- would leave this green forever.

    python build.py

Exit 0 = the guard works, 1 = it does not, 2 = prerequisites missing.
"""

import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import Fail, Ok, Section, cfg, get_scratch_dir, run_capture  # noqa: E402

# Any sketch will do: what is under test is the linker script, not the code.
# iap_probe is already in the tree and builds in a few seconds.
SKETCH = HERE.parent.parent / "onboard" / "iap_probe"

FQBN = "OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743"

# Not 1024-aligned, and deliberately still 512-aligned: an offset that only
# breaks the architectural rule is exactly the case the hardware would accept
# (TBLOFF is bit[31:7], measured 2026-09-21) and that nothing else would catch.
BAD_OFFSET = "0x20200"

# A fragment of the ASSERT text in
# $CORE_REPO/variants/STM32H7xx/H743/ldscript.ld. Matching on it rather than on
# "error" is what makes the negative build prove THIS guard fired, and not some
# unrelated build failure.
EXPECTED_MESSAGE = "must be a multiple of 1024"


def compile_once(label, offset, build_path):
    """Compile the sketch, optionally overriding build.flash_offset."""
    shutil.rmtree(str(build_path), ignore_errors=True)
    # --build-property REPLACES the flag list, so VECT_TAB_OFFSET has to be
    # restated; dropping it changes what is being tested.
    flags = "-DVECT_TAB_OFFSET=" + (offset or "{build.flash_offset}")
    argv = [cfg.ARDUINO_CLI, "compile",
            "--config-file", cfg.ARDUINO_CLI_CONFIG,
            "--fqbn", FQBN,
            "--build-property", "compiler.c.extra_flags=" + flags,
            "--build-property", "compiler.cpp.extra_flags=" + flags]
    if offset:
        argv += ["--build-property", "build.flash_offset=" + offset]
    argv += ["--build-path", str(build_path), str(SKETCH)]
    out, rc = run_capture(argv)
    return out, rc


def main():
    Section("P15 -- the application's start address stays 1024-aligned")
    cli = getattr(cfg, "ARDUINO_CLI", "")
    cli_cfg = getattr(cfg, "ARDUINO_CLI_CONFIG", "")
    if not cli or not Path(cli).exists():
        Fail("arduino-cli not found; set ARDUINO_CLI in config/machine.py")
        return 2
    if not cli_cfg or not Path(cli_cfg).exists():
        Fail("arduino-cli config not found at %s" % cli_cfg)
        return 2

    scratch = Path(get_scratch_dir())
    problems = 0

    Section("positive -- the shipping offset must still link")
    out, rc = compile_once("positive", None, scratch / "p15_ok")
    if rc == 0:
        Ok("  links, as it must")
    else:
        Fail("  the shipping configuration does NOT link -- this is a real break")
        for line in re.split(r"\r?\n", out):
            if re.search(r"error|assert", line, re.I):
                print("    %s" % line)
        problems += 1

    Section("negative -- %s must be refused" % BAD_OFFSET)
    out, rc = compile_once("negative", BAD_OFFSET, scratch / "p15_bad")
    if rc == 0:
        Fail("  it LINKED. The ASSERT in ldscript.ld is gone or cannot fail.")
        Fail("  An image built this way flashes and verifies, then faults on the")
        Fail("  jump -- only BOOT0 gets the board back.")
        problems += 1
    elif EXPECTED_MESSAGE in out:
        Ok("  refused, and the error names the alignment")
        for line in re.split(r"\r?\n", out):
            if EXPECTED_MESSAGE in line:
                print("    %s" % line.strip()[-120:])
                break
    else:
        Fail("  the build failed, but not on the alignment ASSERT")
        Fail("  so this tells us nothing about the guard. Last lines:")
        for line in re.split(r"\r?\n", out)[-6:]:
            if line.strip():
                print("    %s" % line)
        problems += 1

    shutil.rmtree(str(scratch / "p15_ok"), ignore_errors=True)
    shutil.rmtree(str(scratch / "p15_bad"), ignore_errors=True)

    Section("result")
    if problems:
        Fail("%d half(s) of the check did not hold" % problems)
        return 1
    Ok("the linker refuses an unaligned application start address")
    return 0


if __name__ == "__main__":
    sys.exit(main())
