"""Copy the freshly built IAPTool into the Arduino board package.

The IDE's Upload button runs the copy inside the board package, not the one in
Output/. Every build has to land there or the menu keeps driving an old binary
-- see P11 (tools/check_tool_sync.py), which is what catches a forgotten copy.

    python3 tools/install_tool.py            copy every platform it can find
    python3 tools/install_tool.py --check    only report what would be copied

Called at the end of compile_tool.sh, so building is installing. The old binary
is overwritten, not kept: a stale copy beside a fresh one only ever gets picked
up by mistake.

Exit 0 = the package now holds this build, 1 = something could not be copied,
2 = no board package or nothing built.
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import Fail, Ok, Section, Warn, cfg, get_iap_tool  # noqa: E402

# Output/<GOOS> in this repo -> <platform> directory in the board package
TARGETS = [("windows", "win", ".exe"), ("darwin", "macosx", ""), ("linux", "linux", "")]


def package_root():
    """The STM32Tools/<version> directory the IDE actually loads."""
    shipped = get_iap_tool()
    if shipped is None:
        return None
    return Path(shipped).parent.parent          # .../STM32Tools/<ver>/<platform>/IAPTool


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report, do not copy")
    args = ap.parse_args()

    root = package_root()
    if root is None or not root.exists():
        Fail("no board package found - is A15 set in config/machine.py?")
        return 2
    Section("board package")
    print("  %s" % root)

    out_dir = Path(cfg.TOOL_REPO) / "Output"
    Section("copying" if not args.check else "would copy")
    copied, problems = 0, 0
    for goos, plat, ext in TARGETS:
        src = out_dir / goos / ("IAPTool" + ext)
        dst = root / plat / ("IAPTool" + ext)
        if not src.exists():
            Warn("  %-8s no build at %s" % (goos, src))
            continue
        if not dst.parent.exists():
            Warn("  %-8s no %s/ directory in the package" % (goos, plat))
            continue
        print("  %-8s %s -> %s" % (goos, src.name, dst))
        if not args.check:
            try:
                shutil.copy2(src, dst)
                copied += 1
            except OSError as e:
                Fail("  %-8s %s" % (goos, e))
                problems += 1

    Section("result")
    if args.check:
        Ok("nothing written (--check)")
        return 0
    if problems:
        Fail("%d platform(s) could not be copied" % problems)
        return 1
    if copied == 0:
        Fail("nothing was copied - run compile_tool.sh first")
        return 2
    Ok("%d platform(s) installed; the IDE now drives this build" % copied)
    print("  verify with: python tools/check_tool_sync.py   (case P11)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
