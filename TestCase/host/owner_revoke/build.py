"""Builds and runs the R4 harness against the real owner_root_ro.c in the
Arduino core. Case T2-21.

    python build.py

R4 is "the root in force can never revoke itself". It lives in
resolve_chain()/owner_root_ro_is_revoked(), which read the owner-record area as
memory-mapped flash -- so the only way to exercise them on a PC is to hand them
a record area. stubs/fake_owner_area.h does that with -include, which is seen
before the (guarded) default in owner_root_ro.c.

Compiles CORE_REPO rather than CORE_LIVE because that is the version-controlled
copy; P3 is what holds the two identical.

Compiler, first match wins: $CC, then HOST_CC from config/machine.py, then gcc
or clang on PATH. Same order as T1-16 and T4-01 next door.

Exit 0 = every assertion held, non-zero = the compiler or the tests said no.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import EXE, cfg  # noqa: E402

CORE_SRC = Path(cfg.CORE_REPO) / "libraries" / "OpenPLC_IAP" / "src"


def resolve_cc():
    """$CC, HOST_CC, gcc, clang -- first that exists as a path or on PATH."""
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

    Captured rather than inherited so the compiler's diagnostics cannot
    overtake this script's own prints when stdout is a pipe.
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
        print("No C compiler found. Install MinGW-w64 or LLVM/clang and point $HOST_CC "
              "in config/machine.py at its gcc.")
        return 1

    if not CORE_SRC.is_dir():
        print("Not a directory: %s -- check CORE_REPO in config/machine.py" % CORE_SRC)
        return 2

    print("compiler: %s" % cc, flush=True)

    binary = HERE / ("owner_revoke_test" + EXE)
    rc = emit([
        cc, "-std=c11", "-Wall", "-Wextra", "-O0", "-g",
        # Seen before owner_root_ro.c's own default, which is why the record
        # area can be a RAM buffer here without the file being copied.
        "-include", HERE / "stubs" / "fake_owner_area.h",
        "-I", HERE / "stubs",
        "-I", CORE_SRC,
        HERE / "stubs" / "iap_keyderive_stub.c",
        CORE_SRC / "owner_root_ro.c",
        CORE_SRC / "sha256.c",
        CORE_SRC / "fw_verify.c",
        CORE_SRC / "fw_pubkey.c",
        CORE_SRC / "uecc" / "uECC.c",
        HERE / "test_main.c",
        "-o", binary,
    ])
    if rc != 0:
        return rc

    return emit([binary])


if __name__ == "__main__":
    sys.exit(main())
