#!/bin/bash
#
# Builds the fixture firmware and the two PC tools.
#
#   ./build.sh            pick from a menu
#   ./build.sh --fw       fixture firmware only
#   ./build.sh --tool     IAPTool and PortTool only
#   ./build.sh --boot     the bootloader instead of the fixture firmware
#   ./build.sh --all      both, no questions asked
#
# The menu is the point: a person at the bench should not have to remember
# flags. With no terminal to ask (a script, a CI job) it builds both.
#
# It hands off rather than reimplementing: the firmware build drives CubeIDE
# headlessly from TestCase/tools/build_image.py, and compile_tool.sh owns the
# output layout, the three platforms and the copy into the board package that
# case P11 checks.

set -u
cd "$(dirname "$0")"

WHAT=""
case "${1:-}" in
    --fw)   WHAT=fw ;;
    --tool) WHAT=tool ;;
    --boot) WHAT=boot ;;
    --all)  WHAT=all ;;
    "")     WHAT="" ;;
    -h|--help)
        sed -n '3,17p' "$0" | sed 's/^# \{0,1\}//'
        exit 0 ;;
    *)
        echo "不认识的选项：$1" >&2
        echo "用 --fw / --tool / --boot / --all，或者不带参数选菜单。" >&2
        exit 2 ;;
esac

if [ -z "$WHAT" ]; then
    # The menu is only printed to a terminal, but the answer is read either
    # way: a piped choice is how this gets tested, and end of input (a script
    # with nothing on stdin) means nobody is there to ask - build both.
    if [ -t 0 ]; then
        echo ""
        echo "  要编什么？"
        echo "    1) 工装固件 + 两个 PC 工具   (默认)"
        echo "    2) 只编工装固件"
        echo "    3) 只编 IAPTool / PortTool"
        echo "    4) 编 bootloader（不是工装）"
        echo ""
        printf "  选 [1-4，回车=1]: "
    fi
    if read -r PICK; then
        case "${PICK:-1}" in
            1|"") WHAT=all ;;
            2)    WHAT=fw ;;
            3)    WHAT=tool ;;
            4)    WHAT=boot ;;
            *)    echo "没有这一项：$PICK" >&2; exit 2 ;;
        esac
    else
        WHAT=all
    fi
fi

# CubeIDE holds a lock on the workspace, and a headless build cannot take it.
if [ "$WHAT" != "tool" ]; then
    echo ""
    echo "⚠️  编固件前 CubeIDE 要关掉 —— headless 构建拿不到被锁住的 workspace。"
fi

FAILED=0

build_firmware() {
    echo ""
    echo "===== 固件：$1"
    if [ "$1" = "bootloader" ]; then
        ( cd TestCase && python tools/build_image.py ) || FAILED=1
    else
        ( cd TestCase && python tools/build_image.py --porttool ) || FAILED=1
    fi
}

build_tools() {
    echo ""
    echo "===== PC 工具"
    ./compile_tool.sh || FAILED=1
}

case "$WHAT" in
    all)  build_firmware fixture ; build_tools ;;
    fw)   build_firmware fixture ;;
    boot) build_firmware bootloader ;;
    tool) build_tools ;;
esac

echo ""
echo "===== 结果"
if [ "$FAILED" -ne 0 ]; then
    echo "有东西没编过 —— 看上面。"
    exit 1
fi

case "$WHAT" in
    all)  echo "编好了：工装固件 + IAPTool / PortTool" ;;
    fw)   echo "编好了：工装固件" ;;
    boot) echo "编好了：bootloader" ;;
    tool) echo "编好了：IAPTool / PortTool" ;;
esac

# Easy to walk away from, and the next thing anyone does is flash it.
if [ "$WHAT" = "all" ] || [ "$WHAT" = "fw" ]; then
    echo "⚠️  Debug/ 里现在是工装镜像，不是 bootloader。发版前先跑 ./build.sh --boot 换回去。"
fi
