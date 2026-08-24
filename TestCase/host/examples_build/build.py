"""Compiles every example sketch shipped with the core's own libraries. Case P5.

    python build.py                 all of them
    python build.py --only SDRAM    only libraries whose name matches

Why this exists: example sketches are the first thing a new user compiles and
the last thing anybody re-checks. They rot silently -- an API gets renamed, the
examples keep referring to the old name, and nobody notices until someone opens
one and it does not build. Nothing else in the test suite would catch that,
because the examples are not part of any application build.

Scope: only libraries under the core that this project owns. Upstream STM32duino
libraries carry hundreds of examples for boards this variant is not, and
compiling those would report failures nobody intends to fix.

⚠️ Takes about ten minutes, which is why selfcheck deliberately does not run it.

The Python side of M7 step 3, and a drop-in for build.ps1 (whose switches are
spelled -Only and -KeepBuildDirs).

Exit 0 = every example compiled, 1 = at least one did not, 2 = prerequisites
missing.
"""

import argparse
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import Fail, Ok, Section, Warn, cfg, run_capture  # noqa: E402

# Libraries this project owns. Upstream ones are deliberately not listed.
OWN_LIBRARIES = ("OpenPLC_SDRAM", "OpenPLC_IAP", "OpenPLC_Net", "OpenPLC_KNX")

FQBN = ("OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen,xusb=FS,"
        "upload_method=cdcMethod,knxrole=dual_device,downgrade=refuse")


SKIP_DIRS = {"__pycache__", ".vscode", "build"}


def walk_dirs(root):
    """Directories under root, pre-order, name-sorted at each level.

    Get-ChildItem -Directory -Recurse yields a pre-order walk, so a plain
    rglob() sort would put the suite's sections in a different order the first
    time an example grows a subdirectory.
    """
    # Same guard as variant_check: a directory that is not a sketch must not be
    # handed to arduino-cli. Defensive here -- this walks the board package, not a
    # Python tree -- but the two suites should not disagree about what a sketch is.
    for child in sorted(p for p in root.iterdir()
                        if p.is_dir() and p.name not in SKIP_DIRS):
        yield child
        for deeper in walk_dirs(child):
            yield deeper


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--only", default="")
    ap.add_argument("--keep-build-dirs", action="store_true")
    args = ap.parse_args()

    arduino_cli = getattr(cfg, "ARDUINO_CLI", "")
    cli_config = getattr(cfg, "ARDUINO_CLI_CONFIG", "")

    if not arduino_cli or not Path(arduino_cli).exists():
        Fail("arduino-cli not found. Set $ARDUINO_CLI in config/machine.py")
        return 2
    if not cli_config or not Path(cli_config).exists():
        Fail("arduino-cli config not found at %s" % cli_config)
        return 2

    sketches = []
    for lib in OWN_LIBRARIES:
        if args.only and args.only.lower() not in lib.lower():
            continue
        examples = Path(cfg.CORE_LIVE) / "libraries" / lib / "examples"
        if not examples.is_dir():
            continue
        # An Arduino example is a directory holding a .ino of the same name.
        for d in walk_dirs(examples):
            if (d / (d.name + ".ino")).exists():
                sketches.append({"lib": lib, "name": d.name, "path": d})

    if not sketches:
        if args.only:
            Warn("no examples matched --only %s" % args.only)
            return 0
        # Not a pass: the list above says these libraries have examples. None
        # found means either they were deleted or CORE_LIVE is wrong.
        Fail("no example sketches found under %s -- is $CORE_LIVE correct?"
             % cfg.CORE_LIVE)
        return 2

    failed = 0
    for s in sketches:
        Section("%s / %s" % (s["lib"], s["name"]))
        build_path = Path(tempfile.gettempdir()) / ("ex_" + s["lib"] + "_" + s["name"])

        out, rc = run_capture([arduino_cli, "compile", "--warnings", "all",
                               "--config-file", cli_config, "--fqbn", FQBN,
                               "--build-path", build_path, s["path"]])
        lines = re.split(r"\r?\n", out)
        # A captured stream ends with a newline, which split() turns into a
        # trailing empty element. PowerShell's `2>&1` line array has no such
        # element, and it would shift the "last 10 lines" dump by one.
        if lines and lines[-1] == "":
            lines.pop()

        if rc == 0:
            size = re.search(r"Sketch uses \d+ bytes", out)
            Ok("PASS - " + size.group(0) if size else "PASS")
        else:
            Fail("FAIL")
            # Errors only. Matching "error|warning" showed a wall of upstream
            # -Wunknown-pragmas noise and truncated before reaching the actual
            # cause -- the failure looked like it had no explanation.
            errors = [ln for ln in lines
                      if re.search(r"\berror\b|Error during build", ln, re.I)]
            if errors:
                for ln in errors[:12]:
                    print("    %s" % ln)
            else:
                Warn("    no line matched 'error'; last 10 lines of output:")
                for ln in lines[-10:]:
                    print("    %s" % ln)
            failed += 1

        if not args.keep_build_dirs:
            shutil.rmtree(str(build_path), ignore_errors=True)

    Section("result")
    print("  %d example(s) compiled, %d failed" % (len(sketches), failed))
    if failed > 0:
        Fail("%d example(s) do not build" % failed)
        return 1
    Ok("every example builds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
