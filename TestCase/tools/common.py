"""Shared by everything under tools/. Import it first:

    from common import cfg, Section, Ok, Warn, Fail, get_go_bin, ...

This is the Python side of M7 (see open_plc_cube_ide/docs/work/
M7-python-scripts.md). It is a translation of tools/_common.ps1 and must behave
identically to it -- the PowerShell version stays until every case has been
shown to reach the same verdict through both.

Run it directly to see what this machine resolves to:

    python tools/common.py --probe
"""

import glob
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------- platform
# sys.platform says "win32" even on 64-bit Windows, and "darwin" for macOS.
# Normalise once, here, so nothing below ever tests sys.platform again.
if sys.platform.startswith("win"):
    PLATFORM = "windows"
elif sys.platform == "darwin":
    PLATFORM = "macos"
else:
    PLATFORM = "linux"

IS_WIN = PLATFORM == "windows"
EXE = ".exe" if IS_WIN else ""

# Three tool families, three different names for the same three platforms.
# Keeping the mapping here is the whole point: no script below spells any of
# them out.
GOOS_DIR = {"windows": "windows", "linux": "linux", "macos": "darwin"}[PLATFORM]     # compile_tool.sh output layout
A15_DIR = {"windows": "win", "linux": "linux", "macos": "macosx"}[PLATFORM]          # Arduino15 packages/*/tools/STM32Tools/*/
CUBE_PLUG = {"windows": "win32", "linux": "linux64", "macos": "macos64"}[PLATFORM]   # CubeIDE externaltools plugin suffix


# ---------------------------------------------------------------- config
def _load_machine():
    """Import config/machine.py, the one file that differs between machines."""
    path = Path(__file__).resolve().parent.parent / "config" / "machine.py"
    if not path.exists():
        print("config/machine.py is missing.", file=sys.stderr)
        print("Generate it -- this machine's paths are detected, not typed:",
              file=sys.stderr)
        print("    python3 tools/init_machine.py        (python on Windows)",
              file=sys.stderr)
        sys.exit(1)
    import importlib.util
    spec = importlib.util.spec_from_file_location("machine", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cfg = _load_machine()


# ---------------------------------------------------------------- output
# ANSI colours, matching the PowerShell version's Cyan/Green/Yellow/Red. Windows
# consoles only understand them once virtual terminal processing is on, which
# python does not enable for us; NO_COLOR turns them off everywhere.
def _colour_ok():
    if os.environ.get("NO_COLOR"):
        return False
    if not sys.stdout.isatty():
        return False
    if IS_WIN:
        try:
            import ctypes
            k = ctypes.windll.kernel32
            k.SetConsoleMode(k.GetStdHandle(-11), 7)
        except Exception:
            return False
    return True


_COLOUR = _colour_ok()


def _paint(text, code):
    return "\033[%sm%s\033[0m" % (code, text) if _COLOUR else text


def _emit(text):
    """print(), but never crash on a console that cannot encode the text.

    Windows consoles default to a legacy codepage -- GBK on this machine -- and
    these documents are Chinese and full of characters like the warning sign. On
    2026-08-24 P8 found a real duplicated claim and then died with
    UnicodeEncodeError while printing it, exit 1 with a traceback instead of the
    finding. A check whose whole job is to tell you what it found must not be
    silenced by the terminal it happens to run in, so unencodable characters are
    replaced rather than fatal.
    """
    try:
        print(text)
    except UnicodeEncodeError:
        enc = (sys.stdout.encoding or "ascii")
        print(text.encode(enc, "replace").decode(enc, "replace"))


def Section(t): _emit(""); _emit(_paint("===== " + t, "36"))
def Ok(t):      _emit(_paint(t, "32"))
def Warn(t):    _emit(_paint(t, "33"))
def Fail(t):    _emit(_paint(t, "31"))


# ---------------------------------------------------------------- files
def read_text(path):
    """The equivalent of PowerShell's `Get-Content -Raw`.

    Two details matter for M7's output comparison. newline="" keeps CRLF intact,
    because Get-Content -Raw does and a regex capturing to end-of-line would
    otherwise pick up a trailing \\r on one side only. And a UTF-8 BOM is
    stripped, because PowerShell consumes it rather than handing it to the
    caller -- left in, it would break a pattern anchored at the first character.
    """
    with open(str(path), "r", encoding="utf-8", errors="replace", newline="") as fh:
        text = fh.read()
    return text[1:] if text.startswith("﻿") else text


# ---------------------------------------------------------------- document roots
# The product-level documents live in the AI-Skills checkout, not in any of the
# six product repos. Three checks need to find them (P8, P9 and
# check_status_sync), so the locating happens once, here.
#
# SKILLS_REPO comes from config/machine.py because AI-Skills is shared across
# projects and is not a sibling of the product repos here -- it sits one level
# further out. The two-candidate probe below is the fallback for a config written
# before SKILLS_REPO existed, and is why these are functions, not constants.
def skills_repo():
    """The AI-Skills checkout, or None if this machine has no clone of it."""
    configured = getattr(cfg, "SKILLS_REPO", "")
    if configured and Path(configured).is_dir():
        return Path(configured)
    boot = Path(getattr(cfg, "BOOT_REPO", "") or ".")
    for cand in (boot.parent / "AI-Skills", boot.parent.parent / "AI-Skills"):
        if cand.is_dir():
            return cand
    return None


# Points one level ABOVE docs/, so a citation reads $PROD/docs/STATUS.md. Pointing
# it at docs/ itself made $PROD/docs/x.md resolve to .../docs/docs/x.md, which P9
# caught at once.
def prod_docs():
    """$PROD -- where the product-level documents live: what the relationship
    between the repositories is, and what the product as a whole is.

    In the AI-Skills checkout rather than in one of the six product repos,
    because its subject is all of them. Every repo's CLAUDE.md points here by
    name; there is no plugin involved -- reading a document needs a path, not a
    loading mechanism."""
    s = skills_repo()
    return s / "OpenPLC" if s else None


# ---------------------------------------------------------------- paths
def get_scratch_dir():
    """Scratch files (redirected stdout, oversized test images, phase-1 state).

    tempfile honours TMPDIR/TEMP/TMP and falls back to /tmp, so unlike the
    PowerShell version there is no platform test to get wrong here.
    """
    return Path(tempfile.gettempdir())


def get_scratch_file(name):
    return get_scratch_dir() / name


def get_go_bin(name):
    """Where compile_tool.sh and `go build -o Output/...` put binaries for THIS host."""
    return Path(cfg.TOOL_REPO) / "Output" / GOOS_DIR / (name + EXE)


def _newest(pattern):
    hits = sorted(glob.glob(str(pattern)))
    return Path(hits[-1]) if hits else None


def get_iap_tool():
    """IAPTool ships inside the Arduino board package, one directory per platform.

    Its version is independent of the core's, so resolve both by wildcard: a
    package update must not require editing config/.
    """
    override = getattr(cfg, "IAPTOOL", "")
    if override and Path(override).exists():
        return Path(override)
    a15 = getattr(cfg, "A15", "")
    if not a15:
        Fail("IAPTOOL not set and A15 not set in config/machine.py")
        sys.exit(1)
    hit = _newest(Path(a15) / "packages" / "OpenPLC_Alpha" / "tools" / "STM32Tools"
                  / "*" / A15_DIR / ("IAPTool" + EXE))
    if not hit:
        Fail("IAPTool not found under %s for platform '%s' "
             "(looked in .../STM32Tools/*/%s/)" % (a15, PLATFORM, A15_DIR))
        sys.exit(1)
    return hit


def get_programmer_cli():
    """STM32_Programmer_CLI lives in a versioned plugin directory, so resolve it
    by wildcard and take the newest. Hardcoding the version breaks on every
    CubeIDE update -- which is the class of thing config/ exists to avoid.

    The non-Windows plugin suffixes are ST's documented naming and are NOT
    verified; if the lookup fails on Linux, the printed glob is the thing to
    compare against the real install.
    """
    pattern = (Path(cfg.CUBEIDE) / "STM32CubeIDE" / "plugins"
               / ("com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.%s_*" % CUBE_PLUG)
               / "tools" / "bin" / ("STM32_Programmer_CLI" + EXE))
    hit = _newest(pattern)
    if not hit:
        Fail("STM32_Programmer_CLI not found. Looked for: %s" % pattern)
        sys.exit(1)
    return hit


def get_cube_ide_exe():
    """The headless launcher, whose name differs per platform."""
    name = "stm32cubeidec.exe" if IS_WIN else "stm32cubeide"
    exe = Path(cfg.CUBEIDE) / "STM32CubeIDE" / name
    if not exe.exists():
        Fail("%s not found at %s" % (name, exe))
        sys.exit(1)
    return exe


# ---------------------------------------------------------------- processes
def have_cmd(name):
    """PowerShell's `Get-Command <name> -ErrorAction SilentlyContinue`, as a bool.

    Only for names expected on PATH. Settings that deliberately are NOT on PATH
    (HOST_CC) hold an absolute path instead, so test those with Path.exists().
    """
    import shutil
    return shutil.which(name) is not None


def python_exe():
    """This interpreter, for launching sibling scripts.

    The PowerShell twins spell "python", which is wrong on any machine where
    only python3 exists. sys.executable is both correct and guaranteed to be the
    same interpreter that is already running -- neither of which changes a single
    byte of output, so the M7 comparison is unaffected.
    """
    return sys.executable


def run_capture(argv, cwd=None, empty_stdin=False):
    """Run a program and return (merged stdout+stderr, exit code).

    The equivalent of `& prog @args 2>&1 | Out-String -Width 4096`: one string,
    never wrapped at a console width. -Width exists in the PowerShell versions
    because the default wraps at the terminal size and splits the very lines the
    assertions match on; Python has no such trap, but the callers still expect
    one string.

    empty_stdin gives the child an immediately-empty PIPE on stdin, which is what
    PowerShell's `"" | & prog` does. It must be a pipe and not DEVNULL: on Windows
    DEVNULL is NUL, NUL *is* a character device, and Go's os.Stdin.Stat() reports
    ModeCharDevice for it -- so a tool checking "am I attached to a terminal"
    decides yes, prints its prompt, reads EOF and takes the "operator declined"
    branch instead of the "no terminal" branch. DG1's ask-no-console case asserts
    the latter, and DEVNULL made it fail for a reason that had nothing to do with
    what the case is about.
    """
    # input="" is what opens the pipe; subprocess.run refuses to be given both
    # `stdin` and `input`, so the two cases pass different keyword sets.
    kwargs = {"input": ""} if empty_stdin else {}
    proc = subprocess.run([str(a) for a in argv],
                          cwd=None if cwd is None else str(cwd),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace", **kwargs)
    return proc.stdout or "", proc.returncode


def run_emit(argv, cwd=None):
    """Run a program, pass its output straight through, return its exit code.

    For children whose output belongs in this script's own output, where the
    PowerShell twin simply lets the console inherit the stream.

    Two things have to be right for the M7 comparison. Ordering: print() is
    block-buffered when stdout is a pipe, so an inherited child would overtake
    lines printed before it -- hence capture-then-write under this script's
    control, with a flush first. And bytes, not text: a MinGW binary printing
    "\\r\\n" to a text-mode stdout emits "\\r\\r\\n", and universal-newline
    translation would turn that stray "\\r" into an extra blank line that the
    PowerShell version does not produce.
    """
    proc = subprocess.run([str(a) for a in argv],
                          cwd=None if cwd is None else str(cwd),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.stdout:
        sys.stdout.flush()
        sys.stdout.buffer.write(proc.stdout)
        sys.stdout.buffer.flush()
    return proc.returncode


def nonblank_lines(text):
    """`$text -split "\\r?\\n" | Where-Object { $_.Trim() }`.

    Used wherever a failing case dumps a captured log, so the dump keeps the same
    shape on both sides of the comparison.
    """
    return [ln for ln in re.split(r"\r?\n", text) if ln.strip()]


# ---------------------------------------------------------------- serial
def _import_serial():
    """pyserial is the one dependency that is not in the standard library.

    Report it by name rather than dying on an ImportError traceback: a missing
    optional dependency must read as "install this", not as a crash.
    """
    try:
        import serial  # noqa: F401
        return serial
    except ImportError:
        Fail("pyserial is not installed -- serial capture is unavailable.")
        Warn("  pip install pyserial")
        if not IS_WIN:
            Warn("  on Debian:  apt install python3-serial   (or use a venv)")
            Warn("  and make sure the user is in the dialout group for /dev/tty* access.")
        return None


def get_port_holder_hint():
    """Which process is holding a serial port. Neither OS offers a direct answer,
    so this is a best-effort name match against the usual terminals -- enough to
    say "close sscom" instead of "access denied".
    """
    known = ("sscom", "putty", "xshell", "securecrt", "mobaxterm", "ttermpro", "teraterm",
             "realterm", "termite", "hterm", "accessport", "xcom", "uartassist", "arduino",
             "minicom", "picocom", "screen", "cu", "tio")
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/fo", "csv", "/nh"],
                                 capture_output=True, text=True, timeout=10).stdout
            names = [line.split(",")[0].strip('"') for line in out.splitlines() if line]
        else:
            out = subprocess.run(["ps", "-eo", "comm="],
                                 capture_output=True, text=True, timeout=10).stdout
            names = out.split()
    except Exception:
        return None
    hits = sorted({n for n in names for k in known if k in n.lower()})
    return ", ".join(hits) if hits else None


def open_log_ports(ports):
    """Open every port that can be opened; report the ones that cannot and name
    the likely culprit. Returns a dict of name -> Serial.
    """
    serial = _import_serial()
    if serial is None:
        return {}
    open_ports = {}
    for p in ports:
        if not p:
            continue
        try:
            h = serial.Serial(p, cfg.LOG_BAUD, timeout=0.3)
            open_ports[p] = h
            print("listening on %s @ %d" % (p, cfg.LOG_BAUD))
        except Exception as e:
            msg = str(e)
            Warn("cannot open %s: %s" % (p, msg))
            if re.search(r"denied|Permission", msg, re.I):
                who = get_port_holder_hint()
                if who:
                    Warn("  a serial terminal is holding it: %s -- close it and retry" % who)
                if not IS_WIN:
                    Warn("  or the user is not in the dialout group")
    return open_ports


def decode_serial(data):
    """Bytes off a serial port, as text, the same way the PowerShell side sees it.

    ⚠️ ASCII with '?' for anything above 0x7F, and both halves of that matter.

    The PowerShell version reads through .NET SerialPort.ReadExisting(), whose
    Encoding defaults to ASCIIEncoding -- which turns every byte over 0x7F into
    '?'. Decoding as UTF-8 with errors="replace" instead produces U+FFFD, and
    that is wrong in two separate ways:

      1. The two versions then render the SAME bytes as different characters, so
         a board case's captured log differs between them for a reason that has
         nothing to do with the case. M7 step 5 compares exactly that.
      2. U+FFFD cannot be encoded by a GBK console, so printing a capture raised
         UnicodeEncodeError and took the whole script down. This is not a corner
         case: the app's boot emits a stray byte before its "[BOOT]" banner
         (docs/work/ISSUES.md ISS-A2), so it happens on essentially every board run.

    Found 2026-08-22 by running the serial half for the first time -- nothing had
    ever imported it, and all thirteen board scripts are about to.
    """
    return data.decode("ascii", errors="replace").replace("�", "?")


def read_log_ports(open_ports, seconds):
    """Drain the given ports for `seconds` and return name -> captured text."""
    import time
    buf = {k: "" for k in open_ports}
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for k, h in open_ports.items():
            try:
                n = h.in_waiting
                if n:
                    buf[k] += decode_serial(h.read(n))
            except Exception:
                pass
        time.sleep(0.1)
    for h in open_ports.values():
        try:
            h.close()
        except Exception:
            pass
    return buf


# ---------------------------------------------------------------- board
def wait_for_board(ip, timeout=60.0, port=None):
    """Wait until the board answers UDP discovery. Returns True, or False on timeout.

    ⚠️ Needed after every reset that is followed by anything on the network. The
    board takes a second or two to bring the link up and take a DHCP lease, and a
    script that starts talking before then gets "No response, exiting" -- which
    reads exactly like a dead board.

    This asks the same question IAPTool asks, on the same port, so "answered" here
    means the next tool will get an answer too. A plain ICMP ping would come back
    while the IAP server was still not listening.
    """
    import socket
    if port is None:
        port = 56865
    deadline = time.monotonic() + timeout
    payload = b"openplc_server_where_r_y"
    while time.monotonic() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.settimeout(1.0)
            s.sendto(payload, (ip, int(port)))
            s.recvfrom(512)
            return True
        except OSError:
            pass
        finally:
            s.close()
    return False


def assert_target_reachable(cli):
    """Refuses to go further when SWD cannot reach the MCU, and says why. A target
    voltage of 0.00V means the board is unpowered or ST-Link VTREF is not wired --
    VTREF being the one people forget while the other three lines are all correct.
    """
    probe = subprocess.run([str(cli), "-c", "port=SWD", "mode=HOTPLUG"],
                           capture_output=True, text=True).stdout
    m = re.search(r"Voltage\s*:\s*(.+)$", probe, re.M)
    volt = m.group(1).strip() if m else "unknown"
    print("target voltage: %s" % volt)
    # PowerShell's -match is case-insensitive; matching case-sensitively here
    # would let a differently-cased message through as "target reachable".
    if re.search("No STM32 target found", probe, re.I):
        Fail("SWD cannot reach the MCU.")
        if volt.startswith("0.00"):
            Warn("  0.00V -> board unpowered, or ST-Link VTREF/VDD not wired.")
        if not IS_WIN:
            Warn("  on Linux this is also what a missing ST-Link udev rule looks like.")
        sys.exit(1)


# ---------------------------------------------------------------- probe
def warn_config_platform():
    """Say so when config/machine.py was filled in for the other platform.

    The template ships with a Windows value and a commented-out Linux value for
    every path, and asks you to delete the one you are not on. Copy it on Debian
    without editing and every path stays a Windows path -- which shows up below
    as eight unrelated MISSING lines and one actively wrong hint, telling you to
    check a serial adapter when the real answer is that COM5 is not a device
    name on this machine. First Debian run, 2026-08-20, hit exactly that.

    Prints nothing when the config matches the platform, so a correct machine's
    The ENV step is unchanged.
    """
    named = [(n, getattr(cfg, n, "")) for n in
             ("BOOT_REPO", "CORE_REPO", "TOOL_REPO", "CORE_LIVE", "CUBEIDE", "IDE", "A15")]
    if IS_WIN:
        wrong = [n for n, v in named if isinstance(v, str) and v.startswith("/")]
        other = "Linux"
    else:
        wrong = [n for n, v in named
                 if isinstance(v, str) and (re.match(r'^[A-Za-z]:[\\/]', v) or "\\" in v)]
        other = "Windows"
    if not wrong:
        return
    Warn("  config/machine.py still holds %s paths, but this machine is %s."
         % (other, PLATFORM))
    Warn("    %s" % ", ".join(wrong))
    Warn("    The template carries both; delete the block you are NOT on, or the")
    Warn("    wrong assignment silently wins. Everything below is downstream of this.")


def probe(verbose=True):
    """What this machine actually has. This is selfcheck's ENV step.

    Nothing here fails the run: CubeIDE and a serial port are needed to reach the
    board, not to pass the host-side checks.

    Returns the list of missing things, by label.
    """
    if verbose:
        Section("ENV  this machine")
        print("  %-19s %s   (Python %d.%d.%d)" % (
            "platform", PLATFORM,
            sys.version_info[0], sys.version_info[1], sys.version_info[2]))
        warn_config_platform()

    missing = []

    def show(label, path, why):
        if path and Path(path).exists():
            if verbose:
                print("  %-19s %s" % (label, path))
        else:
            if verbose:
                Warn("  %-19s MISSING - %s" % (label, why))
            missing.append(label)

    def show_cmd(label, cmd, why):
        from shutil import which
        found = which(cmd)
        # which() returns the extension cased as PATHEXT spells it, which is
        # upper case by default -- so this would print go.EXE where the
        # PowerShell version prints go.exe. M7 verifies by diffing the two
        # outputs; a cosmetic difference teaches people to ignore diffs.
        if found and IS_WIN:
            p = Path(found)
            found = str(p.with_suffix(p.suffix.lower()))
        if found:
            if verbose:
                print("  %-19s %s" % (label, found))
        else:
            if verbose:
                Warn("  %-19s MISSING - %s" % (label, why))
            missing.append(label)

    show("BOOT_REPO", cfg.BOOT_REPO, "bootloader repo; set it in config/machine.py")
    show("CORE_REPO", cfg.CORE_REPO, "Arduino core repo; set it in config/machine.py")
    show("TOOL_REPO", cfg.TOOL_REPO, "this repo; set it in config/machine.py")
    show("CORE_LIVE", cfg.CORE_LIVE, "install the board package in the Arduino IDE first")
    show_cmd("go", "go", "H1/H3 and every IAPTool build need it")
    show_cmd("python", "python3" if not IS_WIN else "python", "K1-K6 / X1-X2 / DG1 need it")
    show("arduino-cli", cfg.ARDUINO_CLI, "P4 and command-line app builds need it")
    show("CubeIDE", cfg.CUBEIDE, "needed to build and flash the bootloader, not for the checks below")

    # The programmer and IAPTool are resolved by wildcard, so report what the
    # lookup found rather than what config says -- that is the value the scripts
    # will use.
    hit = _newest(Path(cfg.CUBEIDE) / "STM32CubeIDE" / "plugins"
                  / ("com.st.stm32cube.ide.mcu.externaltools.cubeprogrammer.%s_*" % CUBE_PLUG)
                  / "tools" / "bin" / ("STM32_Programmer_CLI" + EXE))
    if hit:
        if verbose:
            print("  %-19s %s" % ("programmer CLI", hit))
    else:
        if verbose:
            Warn("  %-19s MISSING - no '%s' plugin under CubeIDE" % ("programmer CLI", CUBE_PLUG))
        missing.append("programmer CLI")

    hit = _newest(Path(getattr(cfg, "A15", "")) / "packages" / "OpenPLC_Alpha" / "tools"
                  / "STM32Tools" / "*" / A15_DIR / ("IAPTool" + EXE)) if getattr(cfg, "A15", "") else None
    if hit:
        if verbose:
            print("  %-19s %s" % ("shipped IAPTool", hit))
    else:
        if verbose:
            Warn("  %-19s MISSING - no IAPTool for '%s' under the board package"
                 % ("shipped IAPTool", A15_DIR))
        missing.append("shipped IAPTool")

    if verbose:
        print("  %-19s %s" % ("log ports (config)", ", ".join(cfg.LOG_PORTS)))
        for p in cfg.LOG_PORTS:
            if IS_WIN or not p:
                continue
            # COMn is not a device name here. Saying "check the adapter and the
            # dialout group" for one sends the reader after hardware when the
            # config is what needs editing.
            if re.match(r'^COM\d+$', p, re.I):
                Warn("  %-19s %s is a Windows port name -- config/machine.py still has "
                     "the Windows block" % ("", p))
            elif not Path(p).exists():
                Warn("  %-19s %s does not exist -- check the adapter and the dialout group" % ("", p))

    if verbose:
        if missing:
            Warn("  -> %d thing(s) missing on this machine: %s" % (len(missing), ", ".join(missing)))
            Warn("     see open_plc_cube_ide/CLAUDE.md for what each one is and where to get it")
        else:
            Ok("  everything config points at exists")
    return missing


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
        sys.exit(0)
    print(__doc__)
