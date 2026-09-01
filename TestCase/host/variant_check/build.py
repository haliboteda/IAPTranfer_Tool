"""Compiles the variant assertion sketches. Case P4 (selfcheck runs it under that id).

A failure here is a broken variant header, not a broken sketch.

    python build.py

Needs the Arduino IDE's bundled arduino-cli and the OpenPLC core installed;
ARDUINO_CLI and ARDUINO_CLI_CONFIG in config/machine.py point at them.


Exit 0 = every sketch compiled, 1 = at least one did not, 2 = prerequisites
missing.
"""

import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import Fail, Ok, Section, cfg, run_capture  # noqa: E402

# Kept in step with docs/test/BUILD-AND-TEST.md. The menu options matter: the variant
# header is selected by pnum, and a wrong FQBN would compile a different variant
# and prove nothing about this one.
FQBN = ("OpenPLC_Alpha:stm32:OPEN-PLC:pnum=PLC_H743,usb=CDCgen,xusb=FS,"
        "upload_method=cdcMethod,knxrole=dual_device,downgrade=refuse")


def main():
    arduino_cli = getattr(cfg, "ARDUINO_CLI", "")
    cli_config = getattr(cfg, "ARDUINO_CLI_CONFIG", "")

    if not arduino_cli or not Path(arduino_cli).exists():
        Fail("arduino-cli not found. Set $ARDUINO_CLI in config/machine.py")
        return 2
    if not cli_config or not Path(cli_config).exists():
        Fail("arduino-cli config not found at %s" % cli_config)
        return 2

    failed = 0
    # sorted(): Get-ChildItem returns directories in name order, and a suite
    # whose output order depends on the filesystem cannot be compared.
    # Any directory here is treated as a sketch, so skip the ones that are not.
    # __pycache__ appears the moment anything imports a module from this tree, and
    # arduino-cli dutifully tried to compile it -- a FAIL that says nothing about
    # the variant.
    SKIP = {"__pycache__", ".vscode", "build"}
    for sketch in sorted(p for p in HERE.iterdir()
                         if p.is_dir() and p.name not in SKIP):
        Section("compiling %s" % sketch.name)
        build_path = Path(tempfile.gettempdir()) / ("variant_check_" + sketch.name)

        out, rc = run_capture([arduino_cli, "compile", "--warnings", "all",
                               "--config-file", cli_config, "--fqbn", FQBN,
                               "--build-path", build_path, sketch])

        if rc == 0:
            Ok("PASS - the variant's assertions hold")
        else:
            Fail("FAIL - a static_assert fired, or the sketch does not compile:")
            # static_assert messages are the payload here, so show the error
            # lines rather than the usual last-N-lines summary. Case-insensitive
            # deliberately case-insensitive.
            for line in re.split(r"\r?\n", out):
                if re.search(r"error|static_assert|assertion", line, re.I):
                    print("    %s" % line)
            failed += 1

        shutil.rmtree(str(build_path), ignore_errors=True)

    Section("result")
    if failed > 0:
        Fail("%d sketch(es) failed" % failed)
        return 1
    Ok("all variant assertions hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
