"""The .cproject linker-script option must stay a variable, not a filename.

Case P17. $PROD/docs/engineering/HOW-TO-RUN-TESTS.md holds the criterion,
$PROD/docs/build/CUBEMX-RULES.md the background.

CubeMX writes a hardcoded STM32H743IIKX_FLASH.ld into that option on every
generation. Hardcoded, the port-tool build's `-E PLC_LD_SCRIPT=...` has nothing
to act on and that image silently loses its 2048K script. tools/restore_ld_script.bat
undoes it after generation, but that depends on CubeMX running the script after
it writes .cproject -- an ordering ST controls. This check is what catches the
day that ordering changes.

Exit code is the verdict: 0 the option is the variable, 1 it is not.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import Fail, Ok, Section, cfg  # noqa: E402

BOOT = Path(cfg.BOOT_REPO)

WANT = "${workspace_loc:/${ProjName}/${PLC_LD_SCRIPT}}"

# ST's own "||"-separated record string also names the script but carries no
# such id, so this matches the build option alone.
OPTION = re.compile(r'linker\.option\.script[^\n]*?value="([^"]*)"')


def main():
    Section("cproject: linker script is still ${PLC_LD_SCRIPT}")
    cproject = BOOT / ".cproject"
    if not cproject.is_file():
        Fail("%s not found" % cproject)
        return 1

    with open(cproject, "r", encoding="utf-8", newline="") as fh:
        text = fh.read()

    found = OPTION.findall(text)
    if len(found) != 1:
        Fail("expected exactly 1 linker script option, found %d" % len(found))
        return 1

    if found[0] != WANT:
        Fail("linker script option is %s, expected %s" % (found[0], WANT))
        print("      CubeMX overwrote it. Run "
              "$BOOT/tools/restore_ld_script.bat, then check why the")
        print("      after-generation script did not (ProjectManager."
              "UAScriptAfterPath in the .ioc).")
        print("      Left as is, the port-tool image builds against the "
              "bootloader's 128K script.")
        return 1

    Ok("linker script option is %s" % WANT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
