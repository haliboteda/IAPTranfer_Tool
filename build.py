"""Builds the fixture firmware, the bootloader, and the two PC tools.

    python build.py                  pick from a menu
    python build.py --fixture --tool  the fixture firmware and the PC tools
    python build.py --boot --tool     the bootloader and the PC tools (a release)
    python build.py --fixture         just the fixture firmware
    python build.py --boot            just the bootloader
    python build.py --tool            just IAPTool and PortTool

The flags are a set, not a choice: what gets built is two questions - which
firmware, and whether the PC tools come along - and four menu entries could
not cover the six answers. --fixture and --boot together are refused, because
Debug/ holds one image.

⚠️ CubeIDE must be CLOSED for a firmware build: a headless build cannot take a
locked workspace.

⚠️ After a fixture build, Debug/ holds an image that is NOT a bootloader. Run
--boot before releasing.

It hands off rather than reimplementing: the firmware build drives CubeIDE
headlessly through TestCase/tools/build_image.py, and compile_tool.sh owns the
output layout, the three platforms and the copy into the board package that
case P11 checks.

Exit 0 = everything asked for was built, 1 = a build failed, 2 = bad usage.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# A heading has to reach the terminal before the child process writes over it.
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "TestCase" / "tools"))

from common import Fail, Ok, Section, Warn, cfg  # noqa: E402

# Menu entry -> (which firmware or None, build the PC tools).
MENU = [
    ("工装固件 + PC 工具", "　(默认，日常上板调试)", "fixture", True),
    ("bootloader + PC 工具", "　(发版)", "boot", True),
    ("只编工装固件", "", "fixture", False),
    ("只编 bootloader", "", "boot", False),
    ("只编 IAPTool / PortTool", "", None, True),
]


def ask():
    """Returns (firmware, tools) from the menu, or the default with no answer."""
    if sys.stdin.isatty():
        print("")
        print("  要编什么？")
        for i, (name, note, _, _) in enumerate(MENU, 1):
            print("    %d) %s%s" % (i, name, note))
        print("")
        sys.stdout.write("  选 [1-%d，回车=1]: " % len(MENU))
        sys.stdout.flush()
    line = sys.stdin.readline()
    if line == "":
        # Nobody there to ask - a script, a CI job. Build the usual pair.
        return MENU[0][2], MENU[0][3]
    pick = line.strip() or "1"
    if not pick.isdigit() or not (1 <= int(pick) <= len(MENU)):
        Fail("没有这一项：" + pick)
        sys.exit(2)
    _, _, fw, tools = MENU[int(pick) - 1]
    return fw, tools


def build_firmware(which):
    Section("固件：" + ("bootloader" if which == "boot" else "工装"))
    args = [sys.executable, str(HERE / "TestCase" / "tools" / "build_image.py")]
    if which == "fixture":
        args.append("--porttool")
    return subprocess.call(args, cwd=str(HERE / "TestCase")) == 0


def rebuild_sim():
    """The simulated board compiles the same porttool sources as the fixture.

    *** So a firmware change leaves it stale, and H5 then tests yesterday's
    protocol against today's panel. *** Rebuilding it here is what keeps
    "I changed the firmware" from silently meaning "and the simulated board
    still answers the old way". Failing to build it is a warning, not an
    error: it needs a host compiler, and a machine without one can still
    build firmware and tools.
    """
    Section("模拟板")
    rc = subprocess.call([sys.executable, "build.py", "--sim"],
                         cwd=str(HERE / "TestCase" / "host" / "porttool_caps"))
    if rc != 0:
        Warn("模拟板没重建（多半是主机 gcc 没装）—— H5 会用旧的那个。")
    return rc == 0


def find_bash():
    """The bash that can run compile_tool.sh.

    ⚠️ On Windows, `bash` on PATH is System32\\bash.exe - the WSL launcher, not
    a shell. With no distribution installed it prints an install hint and
    exits non-zero, which reads as a build failure. config/machine.py names
    the real one, because where Git is installed is a fact about this machine.
    """
    named = getattr(cfg, "GIT_BASH", "")
    if named and Path(named).exists():
        return named
    found = shutil.which("bash")
    if found and "system32" in found.lower():
        return None      # WSL, not a shell
    return found


def build_tools():
    Section("PC 工具")
    bash = find_bash()
    # ⚠️ On Windows, `bash` on PATH is WSL's, and this machine has no
    # distribution installed. Git Bash is what runs compile_tool.sh, and where
    # it lives is machine-specific - so it belongs in config/machine.py rather
    # than here. Say which one is missing instead of failing with WSL's error.
    if bash is None:
        Fail("找不到能跑 compile_tool.sh 的 bash。Windows 上 PATH 里那个是 WSL 的启动器，"
             "不是 shell。跑一遍：python TestCase/tools/init_machine.py —— 它会把 Git Bash "
             "的位置写进 config/machine.py。")
        return False
    return subprocess.call([bash, str(HERE / "compile_tool.sh")], cwd=str(HERE)) == 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixture", action="store_true", help="工装固件")
    ap.add_argument("--boot", action="store_true", help="bootloader")
    ap.add_argument("--tool", action="store_true", help="IAPTool 和 PortTool")
    args = ap.parse_args()

    if args.fixture and args.boot:
        Fail("--fixture 和 --boot 只能选一个 —— Debug/ 里只放得下一份镜像。")
        return 2

    if args.fixture or args.boot or args.tool:
        fw = "fixture" if args.fixture else ("boot" if args.boot else None)
        tools = args.tool
    else:
        fw, tools = ask()

    if fw is not None:
        print("")
        Warn("编固件前 CubeIDE 要关掉 —— headless 构建拿不到被锁住的 workspace。")

    ok = True
    if fw is not None:
        ok = build_firmware(fw) and ok
        # Only the fixture: the simulated board stands in for that image.
        if ok and fw == "fixture":
            rebuild_sim()
    if tools:
        ok = build_tools() and ok

    Section("结果")
    if not ok:
        Fail("有东西没编过 —— 看上面。")
        return 1
    Ok("编好了：" + " + ".join(
        ([("bootloader" if fw == "boot" else "工装固件")] if fw else []) +
        (["IAPTool / PortTool"] if tools else [])))
    if fw == "fixture":
        Warn("Debug/ 里现在是工装镜像，不是 bootloader。发版前先跑 --boot 换回去。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
