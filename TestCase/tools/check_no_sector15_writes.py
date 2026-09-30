"""No code in the Arduino core writes flash sector 15.

Case P19. $PROD/docs/engineering/HOW-TO-RUN-TESTS.md holds the criterion,
$PROD/docs/build/BOOTLOADER-PROJECT-LAYOUT.md (section 2) the flash layout.

Sector 15 (0x081E0000) holds the calibration values and the firmware metadata,
and only the bootloader may write it: an application that erases it takes both
with it. A library (or cores/<x>, variants/<x>) is flagged when one of its
files calls a flash write or erase API and one of its files names sector 15 --
per library, because the address usually sits in a header macro. Reading
sector 15 (the calibration area) is allowed.

Usage: check_no_sector15_writes.py [<core root>]   (default: $CORE_REPO)
Exit code is the verdict: 0 nothing writes sector 15, 1 something does.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import Fail, Ok, Section, cfg  # noqa: E402

SCAN_DIRS = ("cores", "libraries", "variants")
SUFFIXES = {".c", ".cpp", ".h", ".hpp", ".ino", ".S", ".s"}

WRITES = re.compile(r"HAL_FLASH_Program|HAL_FLASHEx_Erase|FLASH_TYPEPROGRAM|FLASH->CR")
SECTOR15 = (
    re.compile(r"0x081E0000", re.IGNORECASE),
    re.compile(r"FLASH_SECTOR_TOTAL\s*-\s*1"),
)
# Bank 2 sector 7 spelled with the HAL constants; both must appear.
BANK2 = re.compile(r"FLASH_BANK_2")
SECTOR7 = re.compile(r"FLASH_SECTOR_7\b")

COMMENTS = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)


def names_sector15(code):
    if any(p.search(code) for p in SECTOR15):
        return True
    return bool(BANK2.search(code) and SECTOR7.search(code))


def main():
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(cfg.CORE_REPO)
    Section("core: nothing writes flash sector 15")
    if not root.is_dir():
        Fail("%s not found" % root)
        return 1

    writers, namers = {}, {}
    for sub in SCAN_DIRS:
        for f in sorted((root / sub).rglob("*")):
            if f.suffix not in SUFFIXES or not f.is_file():
                continue
            rel = f.relative_to(root)
            unit = Path(*rel.parts[:2])
            code = COMMENTS.sub("", f.read_text(encoding="utf-8", errors="replace"))
            if WRITES.search(code):
                writers.setdefault(unit, rel)
            if names_sector15(code):
                namers.setdefault(unit, rel)

    offenders = sorted(set(writers) & set(namers))
    if offenders:
        for u in offenders:
            Fail("%s writes flash (%s) and names sector 15 (%s)"
                 % (u, writers[u].name, namers[u].name))
        print("      Sector 15 belongs to the bootloader (calibration + firmware metadata).")
        print("      Move the data elsewhere; see section 2 of $PROD/docs/build/BOOTLOADER-PROJECT-LAYOUT.md.")
        return 1

    Ok("no file under %s writes flash sector 15" % ", ".join(SCAN_DIRS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
