"""Shared by run_cases.py and run_downgrade.py -- the two suites that drive the
real IAPTool against fake_board.py.

Everything below used to be copied into each script by hand. That is the shape
treats as a defect: two copies drift, and the port is the moment to stop
carrying them. Anything here that only one suite needs does not belong here.

The leading underscore keeps `import _common` from colliding with tools/common.py,
which is on sys.path ahead of this directory.
"""

import json
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import (EXE, GOOS_DIR, Fail, Ok, Section, Warn, cfg,  # noqa: E402
                    get_go_bin, have_cmd, nonblank_lines, python_exe,
                    read_text, run_capture)

FAKE_BOARD = HERE / "fake_board.py"
DEFAULT_PORT = "56865"


def parse_hex_bytes(text):
    """Every 0xNN in the text, concatenated as lower-case hex."""
    return "".join(re.findall(r"0x([0-9a-fA-F]{2})", text)).lower()


def boot_key_paths():
    """The committed test key pair, as (fw_pubkey.inc, signing key .pem)."""
    keys = Path(cfg.BOOT_REPO) / "IAPServer" / "keys"
    return keys / "fw_pubkey.inc", keys / "fw_signing_key.TEST_ONLY.pem"


def trusted_pubkey_hex():
    """The key the bootloader was built to trust, or None with the reason printed.

    Parsed out of the byte array the build #includes rather than kept as a second
    copy here, which would go stale the first time anyone rotates keys.
    """
    inc_path, _ = boot_key_paths()
    if not inc_path.exists():
        Fail("not found: %s" % inc_path)
        return None
    good_hex = parse_hex_bytes(read_text(inc_path))
    if len(good_hex) != 128:
        Fail("fw_pubkey.inc parsed to %d hex chars, expected 128" % len(good_hex))
        return None
    return good_hex


def resolve_port():
    """Read the port from the same config IAPTool reads, so the two cannot drift
    apart. It is a string in that file, and is passed on as one."""
    cfg_json = Path(cfg.TOOL_REPO) / "local_config.json"
    if cfg_json.exists():
        try:
            value = json.loads(read_text(cfg_json)).get("server_port")
        except ValueError:
            value = None
        if value:
            return str(value)
    return DEFAULT_PORT


def build_iap_tool():
    """The locally built IAPTool, building it first if it is not there yet.

    Deliberately not get_iap_tool(): these suites test the tool in this working
    tree, not the copy shipped inside the board package.
    """
    iap_tool = get_go_bin("IAPTool")
    if iap_tool.exists():
        return iap_tool
    Warn("IAPTool not built, building it now")
    proc = subprocess.run(["go", "build", "-o",
                           "Output/%s/IAPTool%s" % (GOOS_DIR, EXE), "."],
                          cwd=str(cfg.TOOL_REPO))
    if proc.returncode != 0 or not iap_tool.exists():
        Fail("cannot build IAPTool")
        sys.exit(2)
    return iap_tool


def stage_iap_tool(scratch, iap_tool, port):
    """Put a copy of IAPTool in a scratch directory whose config names no key.

    IAPTool resolves local_config.json and its fallback keys/ directory relative
    to its own executable, not the working directory. The "host has no private
    key" cases are therefore unreachable while running the checked-out copy --
    omitting --key just falls back to the signing_key in the repo's config, which
    is how the first version of run-cases silently tested nothing. It also keeps
    the run from depending on whatever the checked-out config happens to say.

    The config is written without a BOM: a BOM-writing editor's UTF-8
    utf8 emits one and Go's json.Unmarshal rejects it, so IAPTool would exit
    before doing anything -- which reads as a broken tool rather than a broken
    config file.
    """
    iap_run = scratch / ("IAPTool" + EXE)
    shutil.copy2(str(iap_tool), str(iap_run))
    scratch_cfg = {
        "server_port": port,
        "signing_key": "",
        "password_file": str(Path(cfg.BOOT_REPO) / "IAPServer" / "keys"
                             / "iap_fixed_password.txt"),
    }
    (scratch / "local_config.json").write_text(json.dumps(scratch_cfg),
                                               encoding="utf-8")
    return iap_run


def fixed_bytes(n, step, offset):
    """Filler with no meaning: nothing on either side inspects the image content
    in the phase under test, and fixed content keeps the run reproducible."""
    return bytes((i * step + offset) % 256 for i in range(n))


def encode_version(iap_run, semver):
    """Encode a semver through IAPTool itself, or None with the reason printed.

    Hardcoding the packed bytes here would be a second copy of encodeSemver, and
    a packed-byte layout is exactly the kind of thing that gets changed once and
    forgotten in one place.
    """
    out, _ = run_capture([iap_run, "version", semver])
    m = re.search(r"(\d+)\s*$", out.strip())
    if not m:
        Fail("cannot encode version %s via IAPTool: %s" % (semver, out.strip()))
        return None
    return m.group(1)


def start_fake_board(scratch, case_id, argv_tail):
    """Launch the stand-in board with its output captured to a log file.

    Returns (process, log path, open file handles). fake_board.py prints with
    flush=True, which is what makes killing it safe: a block-buffered child would
    lose the very lines the downgrade case asserts on.
    """
    log = scratch / ("board_%s.log" % case_id)
    out_fh = open(str(log), "wb")
    err_fh = open(str(log) + ".err", "wb")
    proc = subprocess.Popen([python_exe(), str(FAKE_BOARD)] + [str(a) for a in argv_tail],
                            stdout=out_fh, stderr=err_fh)
    return proc, log, (out_fh, err_fh)


def stop_fake_board(proc, handles, log=None):
    """Stop the stand-in board and make sure it is really gone.

    ⚠️ kill() alone is not enough, and the same trap has
    the same hole -- it just loses the race less often because it is slower.

    Two races, both of which produced a FAIL whose stated reason had nothing to
    do with the case:

    1. The board's listening sockets stay bound until the process actually dies.
       fake_board.py sets SO_REUSEADDR on its TCP socket, so the NEXT case's
       board binds the same port happily and both are listening at once -- which
       of them accepts is undefined. The symptom was a case failing with "board
       was never sent a flash command" while its log held nothing but the startup
       line, because the connections had been served by the previous case's
       process and logged to the previous case's file. wait() closes that window.

    2. IAPTool exits as soon as it has sent the last byte, while the board is
       still printing what it received. Killing it at that moment truncates the
       log the assertions read. settle waits for the log to stop growing first.
    """
    if log is not None:
        settle_log(log)
    proc.kill()
    proc.wait()
    for fh in handles:
        fh.close()


def settle_log(log, quiet=0.25, timeout=3.0):
    """Wait until a log file stops growing, bounded.

    fake_board.py prints with flush=True, so "stopped growing" really does mean
    "has written everything it is going to write" -- there is no buffer left to
    lose. Returns when quiet seconds pass with no new bytes, or at timeout.
    """
    deadline = time.time() + timeout
    last = -1
    stable_since = None
    while time.time() < deadline:
        size = log.stat().st_size if log.exists() else 0
        if size != last:
            last = size
            stable_since = time.time()
        elif stable_since is not None and (time.time() - stable_since) >= quiet:
            return
        time.sleep(0.05)


def wait_for_listener(port):
    """Wait for the stand-in board rather than sleeping a fixed amount."""
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=1):
                return True
        except OSError:
            time.sleep(0.1)
    return False
