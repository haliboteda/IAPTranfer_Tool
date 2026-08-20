"""Unit tests for init_machine's prompt. Run from TestTool/:

    python3 tools/test_init_machine.py

The prompt cannot be exercised the way it is used: it refuses to ask unless
stdin is a terminal, so a pipe cannot reach it and an agent cannot either. That
leaves quote stripping, tilde expansion and the install-root validators shipped
untested, and those are the parts most likely to be wrong -- pasting the parent
of the CubeIDE root, or a path copied from a file manager with its quotes still
attached, are the two things a person is most likely to do.

Exit 0 = all pass, 1 = at least one failed, 2 = the machine lacks the paths
these cases need (they use real directories, because the validators check the
filesystem; a fake path would only prove the error branch).
"""

import builtins
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import init_machine as im  # noqa: E402

fails = 0


def run(name, answers, key, kind, required, expect):
    """Answers are consumed in order; running out raises EOFError, which is what
    a real closed stdin does. Raising StopIteration instead would sail past the
    handler in ask_for and prove nothing."""
    global fails
    it = iter(answers)

    def fake_input(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    builtins.input = fake_input
    got = im.ask_for(key, kind, required)
    ok = got == expect
    print("  [%s] %-48s got=%r" % ("PASS" if ok else "FAIL", name, got))
    if not ok:
        print("         expected=%r" % (expect,))
        fails += 1


def main():
    # Two real install roots are needed: one CubeIDE and one Arduino IDE, so the
    # validators have something true to accept. Without them the cases would all
    # collapse into "rejected", which is the branch that needs a partner.
    cube = im.detect_cubeide()
    ide = im.detect_ide()
    if not cube or not ide:
        print("SKIP - these cases need a real CubeIDE and Arduino IDE on this machine")
        print("       CubeIDE: %s" % (cube or "not found"))
        print("       IDE    : %s" % (ide or "not found"))
        return 2
    cube_parent = str(Path(cube).parent)
    home = os.path.expanduser("~")

    print("=== ask_for ===")
    run("empty answer skips an optional setting", [""], "CUBEIDE", "path", False, None)
    run("quotes from a file manager are stripped",
        ['"%s"' % cube], "CUBEIDE", "path", False, cube)
    run("the parent of the install root is rejected, then accepted",
        [cube_parent, cube], "CUBEIDE", "path", False, cube)
    run("a path that does not exist is re-asked",
        [os.path.join(cube, "definitely-not-here"), ide], "IDE", "path", False, ide)
    run("ports are split on commas",
        ["/dev/ttyUSB0, /dev/ttyACM0"], "LOG_PORTS", "ports", False,
        ["/dev/ttyUSB0", "/dev/ttyACM0"])
    run("a required setting asks twice before giving up",
        ["", ""], "BOOT_REPO", "path", True, None)
    run("EOF is a skip, not a crash", [], "CUBEIDE", "path", False, None)
    run("a tilde is expanded", ["~"], "WORKSPACE", "path", False, home)

    print()
    if fails:
        print("%d failure(s)" % fails)
        return 1
    print("all cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
