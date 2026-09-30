"""Builds and runs the sector-15 reclaim harness against the real
bootloader_state.c, bkp_stash.c and owner_slot.c. Case T2-34.

    python build.py

A reclaim is seven flash operations: erase, calibration, the revocation, the
owner record (body, header), the metadata record, the marker. The power is cut
before each of them in turn, and the next boot is run three ways: backup copy
intact, battery dead, backup copy corrupted. Criteria:
$PROD/docs/modules/M2-ownership.md (T2-34) and M1/SECTOR-15.md.

Compiler, first match wins: $CC, then HOST_CC from config/machine.py, then gcc
or clang on PATH. Same order as the other host harnesses.

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

IAP_SERVER = Path(cfg.BOOT_REPO) / "IAPServer"
SHARED_STUBS = HERE.parent / "owner_revoke" / "stubs"

RECLAIM_OPS = 7


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


def emit(argv, cwd=None):
    """Run a child, re-emit its merged output verbatim, return its exit code."""
    proc = subprocess.run([str(a) for a in argv], cwd=cwd,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.stdout:
        sys.stdout.flush()
        sys.stdout.buffer.write(proc.stdout)
        sys.stdout.buffer.flush()
    return proc.returncode


def scenarios():
    """(title, [argv...]) -- each argv is one process, run in order."""
    yield "factory sector", [["fresh"]]
    yield "old layout", [["legacy"]]
    for k in range(RECLAIM_OPS + 1):
        calib = "0" if k == 1 else "1"   # cut between erase and calibration write
        yield ("cut before op %d, backup copy intact" % k,
               [["cut", str(k)], ["boot", "1", calib]])
    for k in range(RECLAIM_OPS):
        calib = "0" if k == 1 else "1"
        root = "1" if k == 0 else "0"    # before the erase nothing was lost
        for how in ("battery-dead", "corrupt"):
            yield ("cut before op %d, %s" % (k, how),
                   [["cut", str(k)], [how], ["boot", root, calib]])


def main():
    cc = resolve_cc()
    if not cc:
        print("No C compiler found. Install MinGW-w64 or LLVM/clang and point $HOST_CC "
              "in config/machine.py at its gcc.")
        return 1
    if not IAP_SERVER.is_dir():
        print("Not a directory: %s -- check BOOT_REPO in config/machine.py" % IAP_SERVER)
        return 2

    print("compiler: %s" % cc, flush=True)
    binary = HERE / ("sector15_reclaim_test" + EXE)
    rc = emit([
        cc, "-std=c11", "-Wall", "-Wextra", "-O0", "-g",
        "-include", HERE / "stubs" / "host_s15.h",
        "-I", HERE / "stubs",
        "-I", SHARED_STUBS,
        "-I", IAP_SERVER,
        "-I", IAP_SERVER / "uecc",
        HERE / "stubs" / "fake_flash.c",
        SHARED_STUBS / "iap_keyderive_stub.c",
        IAP_SERVER / "bootloader_state.c",
        IAP_SERVER / "bkp_stash.c",
        IAP_SERVER / "owner_slot.c",
        IAP_SERVER / "sha256.c",
        IAP_SERVER / "fw_verify.c",
        IAP_SERVER / "uecc" / "uECC.c",
        HERE / "test_main.c",
        "-o", binary,
    ])
    if rc != 0:
        return rc

    for title, runs in scenarios():
        print("===== %s" % title, flush=True)
        for argv in runs:
            rc = emit([binary] + argv, cwd=HERE)
            if rc != 0:
                print("FAILED in: %s %s" % (title, " ".join(argv)))
                return rc
    print("\nT2-34: every scenario passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
