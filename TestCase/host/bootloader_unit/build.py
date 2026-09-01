"""Builds and runs the host-side IAP security test harness against the real
bootloader source in open_plc_cube_ide/IAPServer. Case H2.

    python build.py

build.sh is the POSIX twin: same compiler resolution, same flags, same file
list, same output, same exit code.

Compiler, first match wins: $CC, then HOST_CC from config/machine.py, then gcc
or clang on PATH. HOST_CC exists because a compiler installed for this one
purpose does not belong on PATH -- and because "install a compiler" is a
machine-local fact, which config/machine.py is the only home for.

Why the child's output is captured and re-emitted rather than inherited: print()
is block-buffered when stdout is a pipe, which is exactly how the M7 comparison
harness runs this. Left inherited, the compiler's diagnostics would overtake the
"compiler:" line and the two versions would differ in ordering only. Capturing
puts the ordering back under this script's control.

Exit 0 = every assertion held, non-zero = the compiler or the tests said no.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import EXE, cfg  # noqa: E402

IAP_SERVER = Path(cfg.BOOT_REPO) / "IAPServer"


def resolve_cc():
    """$CC, HOST_CC, gcc, clang -- first one that exists as a path or on PATH.

    An absolute path is tried first because HOST_CC deliberately is not on PATH.
    Returns the candidate exactly as written, since that string is printed and
    kept deliberately terse.
    """
    import os
    import shutil
    for cand in (os.environ.get("CC"), getattr(cfg, "HOST_CC", ""), "gcc", "clang"):
        if not cand:
            continue
        if Path(cand).exists():
            return cand
        if shutil.which(cand):
            return cand
    return None


def emit(argv):
    """Run a child, re-emit its merged output verbatim, return its exit code.

    Bytes, deliberately, not text=True. The bootloader's own printf calls end in
    "\\r\\n", and a MinGW binary writing that to a text-mode stdout emits
    "\\r\\r\\n". Python's universal-newline translation turns the stray "\\r"
    into a second line break, so the same test run gains a blank line that is not
    in the child's actual output. Passing the child's bytes straight through
    reproduces what it really wrote.
    """
    proc = subprocess.run([str(a) for a in argv],
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.stdout:
        sys.stdout.flush()
        sys.stdout.buffer.write(proc.stdout)
        sys.stdout.buffer.flush()
    return proc.returncode


def main():
    cc = resolve_cc()
    if not cc:
        print("No C compiler found. Install MinGW-w64 or LLVM/clang, point $HOST_CC in "
              "config/machine.py at its gcc.exe, and re-run -- or run build.sh under "
              "WSL/Git Bash.")
        return 1

    print("compiler: %s" % cc, flush=True)

    binary = HERE / ("iap_hosttest" + EXE)
    rc = emit([
        cc, "-std=c11", "-Wall", "-Wextra", "-O0", "-g",
        "-I", HERE / "stubs",
        "-I", IAP_SERVER,
        HERE / "stubs" / "hal_stub.c",
        HERE / "stubs" / "bootloader_state_stub.c",
        IAP_SERVER / "sha256.c",
        IAP_SERVER / "iap_keyderive.c",
        IAP_SERVER / "iap_auth.c",
        HERE / "test_main.c",
        "-o", binary,
    ])
    if rc != 0:
        return rc

    return emit([binary])


if __name__ == "__main__":
    sys.exit(main())
