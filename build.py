"""Builds everything this bench needs, from one command.

    python build.py             the fixture firmware and both PC tools
    python build.py --fw        the fixture firmware only
    python build.py --boot      the bootloader instead of the fixture firmware
    python build.py --tool      the PC tools only (IAPTool and PortTool)

Two builds live in two places for good reasons - the firmware one drives
CubeIDE headlessly from TestCase/tools/, the tools one cross-compiles Go and
copies IAPTool into the board package. This is the front door to both, so a
change touching firmware and panel together is one command rather than two
remembered paths.

⚠️ CubeIDE must be CLOSED for a firmware build: a headless build cannot take a
locked workspace.

⚠️ After --fw, Debug/ holds the PORT TOOL image, which is not a bootloader.
Run --boot to put the bootloader back before releasing anything.

Exit 0 = everything asked for was built, 1 = a build failed, 2 = setup.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# Sections have to reach the terminal before the child process writes over
# them - a buffered heading arrives after the build it announces.
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "TestCase" / "tools"))

from common import Fail, Ok, Section, Warn  # noqa: E402


def run_firmware(which):
    """Hands off to the headless CubeIDE build."""
    Section("firmware: " + which)
    args = [sys.executable, str(HERE / "TestCase" / "tools" / "build_image.py")]
    if which == "fixture":
        args.append("--porttool")
    return subprocess.call(args, cwd=str(HERE / "TestCase")) == 0


def run_tools():
    """Hands off to compile_tool.sh.

    *** Not reimplemented here. *** That script owns the output layout, the
    three platforms and the copy into the board package, and a second version
    of those rules would be one more thing to keep in step (case P11 checks the
    copy). What this adds is a front door, not a second build.
    """
    Section("PC tools")
    sh = HERE / "compile_tool.sh"
    bash = shutil.which("bash")
    if bash is None:
        Fail("bash not found - compile_tool.sh needs it. On Windows that is "
             "Git Bash; run it by hand, or add its bin directory to PATH.")
        return False
    return subprocess.call([bash, str(sh)], cwd=str(HERE)) == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fw", action="store_true", help="fixture firmware only")
    ap.add_argument("--boot", action="store_true", help="bootloader instead of the fixture")
    ap.add_argument("--tool", action="store_true", help="PC tools only")
    args = ap.parse_args()

    want_fw = args.fw or args.boot or not args.tool
    want_tool = args.tool or not (args.fw or args.boot)

    ok = True
    if want_fw:
        ok = run_firmware("bootloader" if args.boot else "fixture") and ok
    if want_tool:
        ok = run_tools() and ok

    Section("result")
    if not ok:
        Fail("something did not build - see above")
        return 1
    Ok("built: " + ", ".join(
        ([("bootloader" if args.boot else "fixture firmware")] if want_fw else []) +
        (["IAPTool and PortTool"] if want_tool else [])))
    if want_fw and not args.boot:
        Warn("Debug/ now holds the PORT TOOL image, not a bootloader. "
             "Run build.py --boot before releasing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
