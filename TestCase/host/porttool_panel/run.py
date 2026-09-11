"""Clicks through the port tool panel in a real browser. Case H5.

    python run.py --port COM12          the board's RS232 control port
    python run.py --port COM12 --show   watch it happen in a visible window

Why a browser and not the HTTP API: case H4 already covers the protocol and the
parser, and the panel's own API is covered by the Go tests. What neither of them
can see is whether the page a person actually looks at renders the right thing
and whether its buttons do what they say - and that is where the bugs were
found on 2026-09-08 (a lost reply and a corrupted command, both invisible to
the parser tests).

Needs a board: the panel's whole job is talking to one, so a panel test without
a board would only exercise the empty state. It never writes flash and never
drives an output that needs wiring; it starts and stops sessions.

Requires playwright and a Chrome install:
    python -m pip install playwright
The system Chrome is used (channel="chrome"), so no browser is downloaded.

Exit 0 = every check held, 1 = a check failed, 2 = could not run at all.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

# Progress has to be visible while this runs, not only when it exits: piped
# stdout is block-buffered by default, and a stuck run then looks identical to
# a slow one.
try:
    sys.stdout.reconfigure(line_buffering=True)
except AttributeError:
    pass
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

from common import EXE, cfg, Section, Ok, Fail, Warn  # noqa: E402

failures = []


def check(cond, what, detail=""):
    if cond:
        Ok("PASS  %s" % what)
    else:
        Fail("FAIL  %s%s" % (what, ("  --  " + detail) if detail else ""))
        failures.append(what)
    return cond


def panel_exe():
    p = Path(cfg.TOOL_REPO) / "Output" / "windows" / ("PortTool" + EXE)
    if not p.exists():
        Fail("no PortTool at %s - run compile_tool.sh" % p)
        sys.exit(2)
    return p


def port_map_path(exe):
    return exe.parent / "porttool_ports.json"


def start_panel(exe):
    """Starts the panel and returns (process, base url, log path).

    The panel picks its own port, so the address is read from its output rather
    than guessed.

    Its output goes to a FILE, not a pipe. With a pipe, this script reads the
    address line and then stops reading, the panel keeps writing, the pipe
    fills, and the panel blocks - after which it does not even see terminate().
    That deadlock hung a completed run on 2026-09-08: every check had passed
    and the process would not exit. A file also leaves the panel's own log
    behind when something needs explaining.
    """
    log = Path(tempfile.gettempdir()) / "porttool_panel_test.log"
    fh = open(log, "w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen([str(exe), "--no-browser"], cwd=str(exe.parent),
                            stdout=fh, stderr=subprocess.STDOUT)
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            Fail("the panel exited immediately - see %s" % log)
            sys.exit(2)
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        m = re.search(r"http://(127\.0\.0\.1:\d+)", text)
        if m:
            return proc, "http://" + m.group(1), log
        time.sleep(0.2)
    Fail("the panel never printed its address - see %s" % log)
    proc.kill()
    sys.exit(2)


def pids_of(names):
    """The process ids currently running under any of `names`."""
    out = set()
    for name in names:
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq " + name,
                                "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, errors="replace")
        except OSError:
            continue
        for line in (r.stdout or "").splitlines():
            parts = [p.strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[1].isdigit():
                out.add(parts[1])
    return out


def kill_new(names, before):
    """Kills only the processes this run started.

    browser.close() hangs with channel="chrome" on this machine - confirmed
    2026-09-08 by instrumenting the teardown: leaving the page returns, closing
    the browser never does. Rather than wait on an SDK that will not answer,
    the run notes which chrome and driver processes existed beforehand and ends
    exactly the ones it added. Killing by image name would take somebody's
    other browser with it.
    """
    for pid in sorted(pids_of(names) - before):
        subprocess.run(["taskkill", "/F", "/T", "/PID", pid],
                       capture_output=True)


BROWSER_IMAGES = ("chrome.exe", "node.exe")


def panel_source():
    """The panel page as it is embedded in the executable."""
    return (Path(cfg.TOOL_REPO) / "internal" / "ptpanel" / "web" / "index.html")


def node_exe():
    """Playwright ships a node; there is no need for one on PATH."""
    try:
        import playwright
    except ImportError:
        return None
    cand = Path(playwright.__file__).parent / "driver" / ("node" + EXE)
    return cand if cand.exists() else None


def check_page_parses():
    """Fails on a broken page before anything is launched.

    A syntax error in the page does not look like one from the outside: the
    panel serves it happily, the browser stops at the first bad token, nothing
    renders, and every check below times out on a selector. That is exactly how
    a stray line cost a full run on 2026-09-08 - the reported failure was
    "waiting for #portlist .p", nowhere near the mistake.
    """
    src = panel_source()
    if not src.exists():
        Fail("no panel page at %s" % src)
        failures.append("panel page missing")
        return
    js = "\n".join(re.findall(r"<script>(.*?)</script>", src.read_text(encoding="utf-8"), re.S))
    node = node_exe()
    if node is None:
        Warn("no node to parse the page with - skipping the syntax check")
        return
    tmp = Path(tempfile.gettempdir()) / "porttool_panel_page.js"
    tmp.write_text(js, encoding="utf-8")
    r = subprocess.run([str(node), "--check", str(tmp)],
                       capture_output=True, text=True, errors="replace")
    check(r.returncode == 0, "the panel page parses",
          (r.stderr or r.stdout or "").strip()[:400])


def watch_for_page_errors(page):
    """Any error the page throws fails the run, wherever it happened.

    Without this a runtime error is silent: the page half-renders and the
    checks below report whatever is left, which reads as a product bug in the
    wrong place.
    """
    def on_error(err):
        Fail("the page threw: %s" % str(err).splitlines()[0])
        failures.append("page error")

    def on_console(msg):
        if msg.type != "error":
            return
        # A failed request is reported without its URL in the text, so the
        # location is what says which one. Chrome asks for /favicon.ico on
        # every page whether one is offered or not.
        where = (msg.location or {}).get("url", "")
        if "favicon" in where:
            return
        Fail("the page logged an error: %s  (%s)" % (msg.text[:200], where))
        failures.append("console error")

    page.on("pageerror", on_error)
    page.on("console", on_console)


def stop_panel(proc):
    """Ends the panel, and does not wait forever for it to agree."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    proc.kill()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        Warn("the panel would not exit; leaving it to the OS")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", required=True, help="the board's RS232 control port")
    ap.add_argument("--show", action="store_true", help="visible browser window")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        Fail("playwright is not installed - python -m pip install playwright")
        return 2

    exe = panel_exe()

    Section("the page itself")
    check_page_parses()

    # The first half of this test is the first-run state - nothing remembered,
    # so nothing preselected and connect refused. The mapping the panel writes
    # is checked at the end instead, which covers both without needing a second
    # panel process.
    saved = port_map_path(exe)
    if saved.exists():
        saved.unlink()

    browsers_before = pids_of(BROWSER_IMAGES)

    Section("panel")
    proc, base, log = start_panel(exe)
    print("  %s   control port %s" % (base, args.port))
    print("  panel log %s" % log)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(channel="chrome", headless=not args.show)
            # accept_downloads, or the log export check below has nothing to
            # catch: Chrome would just drop the file on the floor.
            page = browser.new_page(accept_downloads=True)
            page.set_default_timeout(10000)
            watch_for_page_errors(page)
            page.goto(base)
            run_checks(page, args.port)

            check_remembered(saved, args.port)

            # Leave the page before closing the browser. It holds an SSE
            # connection to the panel, and closing the browser with that open
            # left playwright's driver waiting forever on 2026-09-08 - every
            # check had passed and the run would not end.
            # Not browser.close(): see kill_new. Leaving the page first so
            # the SSE connection to the panel is dropped politely.
            page.goto("about:blank")
    finally:
        stop_panel(proc)
        kill_new(BROWSER_IMAGES, browsers_before)

    Section("result")
    if failures:
        Fail("%d check(s) failed" % len(failures))
        code = 1
    else:
        Ok("every check held")
        code = 0

    # The work is done and reported. Playwright's driver can outlive this
    # process, and waiting politely for it is how a finished run turns into a
    # hung one - so say the answer, then go.
    sys.stdout.flush()
    os._exit(code)


def check_channel_labels(page):
    """Every per-channel control says which channel AND which terminal.

    The two numberings are offset: dout's channel 3 is terminal A05. A box
    labelled only A03 is the first channel, and somebody who wants DO3 will
    reach for it - which is exactly what happened on 2026-09-10.

    And the channel checkboxes have to be on the card next to the values. They
    were only in the left tree, which is a different panel with a different
    numbering in front of it.
    """
    Section("channel labels and the channel picker")

    page.locator('.tab[data-tab="manual"]').click()
    page.wait_for_selector(".prow")
    rows = page.locator(".prow")
    picked = False
    for i in range(rows.count()):
        if rows.nth(i).get_attribute("data-port") == "dout":
            rows.nth(i).click()
            page.wait_for_timeout(300)
            picked = True
            break
    if not check(picked, "dout is in the port list"):
        return

    card = page.locator('.card[data-port="dout"]')
    if not check(card.count() > 0, "the dout card is up"):
        return
    text = card.first.inner_text()

    # The name the engineer uses, not the terminal number (user 2026-09-11).
    # The terminal range still rides on the card header for whoever wires it.
    flat = " ".join(text.split())
    check("DO3" in flat and "dout3" not in flat,
          "a control names the channel the way the engineer does",
          str([l for l in text.splitlines() if "DO3" in l][:2]))
    check("端子排 A" in flat and "A03-A10" in flat,
          "and the card header still says where to wire it",
          str([l for l in text.splitlines() if "A03-A10" in l][:2]))

    check("测哪几路" in text,
          "the channel picker is on the card, not only in the tree")

    # A parameter with no unit and no range is one the reader has to guess at.
    check("占空比" in text and "Hz" in text,
          "duty and freq say what they are, in units", text[:400])
    check("0..100" in text,
          "and the range comes from the board's own limits line", text[:400])

    # Ticking a box on the card has to move the same state the tree shows.
    boxes = card.first.locator(".chvals input[type=checkbox]")
    if check(boxes.count() >= 8, "there is one checkbox per channel",
             "%d boxes" % boxes.count()):
        # Scoped to the duty grid, not the whole card: the picker lists every
        # channel whether it is selected or not, so its own labels would make
        # this assertion pass for the wrong reason.
        def duty_labels():
            box = page.locator('.card[data-port="dout"]').first \
                      .locator('.chvals[data-param="duty"]').first
            return " ".join(box.inner_text().split())

        check("DO3" in duty_labels(),
              "the duty grid has a box for channel 3 to start with", duty_labels())

        boxes.nth(2).uncheck()
        page.wait_for_timeout(400)
        check("DO3" not in duty_labels(),
              "unticking a channel on the card drops its value box too",
              duty_labels())

        # The tree is the other place the same state is shown; both read the
        # same picked set, so a tick in one has to be a tick in the other.
        tree = page.locator("#treebody").inner_text()
        check('data-port="dout"' in page.locator("#treebody").inner_html(),
              "the tree is still there to agree with")

        page.locator('.card[data-port="dout"]').first \
            .locator(".chvals input[type=checkbox]").nth(2).check()
        page.wait_for_timeout(400)
        check("DO3" in duty_labels(),
              "and ticking it back brings the value box in again", duty_labels())


def pick_port(page, name):
    """Clicks the row for one port. The rows read in Chinese, so the port is
    identified by its data attribute rather than by what is printed."""
    page.locator('.tab[data-tab="manual"]').click()
    page.wait_for_selector(".prow")
    rows = page.locator(".prow")
    for i in range(rows.count()):
        if rows.nth(i).get_attribute("data-port") == name:
            rows.nth(i).click()
            page.wait_for_timeout(300)
            return True
    return False


def check_off_plan_params(page):
    """Changing a parameter has to withdraw the verdict, not fail the board.

    The criteria come from the plan and were written for the plan's parameters.
    Pick one channel out of eight and halve its duty, and ch1/ch8 are no longer
    in the frame at all - so every criterion misses and a healthy board reads
    as failed. That is what a user hit on 2026-09-11: DO3 at 50 %, one press,
    1.4 s, 失败. Nothing in this suite had ever pressed the button with
    anything other than the plan's own parameters, which is why it survived.
    """
    Section("changing a parameter withdraws the verdict")

    if not check(pick_port(page, "dout"), "dout is in the port list"):
        return
    card = page.locator('.card[data-port="dout"]').first
    row0 = page.locator(".startrow").first.inner_text()
    check("单发" in row0 and "连续" in row0,
          "the card offers 单发 / 连续 and says what each is for", " ".join(row0.split())[:120])
    check(page.locator(".startrow button").first.inner_text().strip() == "开始",
          "one button, not two pairs of them")

    # The user's own case: one channel out of eight, at half duty.
    boxes = card.locator('.chvals input[type=checkbox]')
    for i in range(boxes.count()):
        if i != 2 and boxes.nth(i).is_checked():
            boxes.nth(i).uncheck()
            page.wait_for_timeout(120)
    # By channel, not by position: unticking a channel rebuilds the grid, so
    # ".first" can land on the box that was there a render ago - and the 50
    # then goes to DO1 while DO3 keeps the plan's 100. That is a test bug, but
    # it is the kind that reads as a product bug.
    sel = ('.card[data-port="dout"] .chvals[data-param="duty"] '
           'input[type=number][data-ch="3"]')
    for _ in range(50):
        if page.locator(sel).count() == 1:
            break
        page.wait_for_timeout(100)
    page.locator(sel).fill("50")
    page.wait_for_timeout(500)
    check(page.locator(sel).input_value() == "50",
          "the duty box for DO3 holds what was typed",
          page.locator(sel).input_value())

    row = page.locator(".startrow").first.inner_text()
    check("不给结论" in row, "the card says up front that it will not judge",
          " ".join(row.split())[:140])
    check("恢复方案参数" in row, "and offers to put the plan's parameters back", row)
    # 细节写在卡片顶上的说明块里（按钮旁边只留一句短的，免得同一段话印两遍）
    note = page.locator(".startrow .note").first.inner_text()
    check("DO3" in note and "50" in note, "and names what was changed",
          " ".join(note.split())[-200:] +
          "  |  edited=" + str(page.evaluate("JSON.stringify(edited)")))

    # Press it. The board is fine, so the one thing that must not happen is 失败.
    page.locator(".startrow button.primary").first.click()
    for _ in range(400):
        if "跑着" not in page.locator(".startrow").first.inner_text():
            break
        page.wait_for_timeout(100)
    page.wait_for_timeout(600)
    stat = page.locator('.prow[data-port="dout"] .stat').inner_text().strip()
    check(stat != "失败", "a healthy board is not reported as failed", stat)
    check("不给结论" in page.locator(".startrow").first.inner_text(),
          "and the card says why there is no verdict",
          page.locator(".startrow").first.inner_text())

    # And it has to be one click back to a state that does judge.
    for b in range(page.locator(".startrow button").count()):
        if "恢复" in page.locator(".startrow button").nth(b).inner_text():
            page.locator(".startrow button").nth(b).click()
            break
    page.wait_for_timeout(600)
    check("不给结论" not in page.locator(".startrow").first.inner_text(),
          "restoring the plan's parameters makes it judge again",
          " ".join(page.locator(".startrow").first.inner_text().split())[:120])


# What must never be printed on a card again. Every one of these was on screen
# on 2026-09-11, when the user said: 页面上英文的地方没有翻译成中文.
#
# *** Not a general "no ASCII" rule on purpose. Terminal numbers (A03-A10),
# *** part numbers (LAN8742A, ISO1044, LM50) and units (Hz, mV) are what the
# *** engineer reads off the board and the schematic - translating those would
# *** make the panel harder to use, not easier.
BANNED_ON_CARDS = [
    "Klemmblock", "duty", "freq", "period", "miss", "seq=", "rxlines",
    "hold", "blink", "extloop", "loopback", "autoneg", "mismatches",
    "observed", "detected", "Digital Out", "AOUT", "SD Karte", "SDram",
    "temp1", "temperature",
]


def check_nothing_in_english(page):
    """Every port card, swept for the protocol words a person should not meet.

    The panel is read in Chinese (user 2026-09-11). Nothing enforced that, so
    a field name added to the firmware arrived on screen in English and stayed
    there until somebody complained.
    """
    Section("no protocol words left on the cards")

    page.locator('.tab[data-tab="manual"]').click()
    page.wait_for_selector(".prow")
    rows = page.locator(".prow")
    ports = [rows.nth(i).get_attribute("data-port") for i in range(rows.count())]
    bad = {}
    for name in ports:
        if not name or not pick_port(page, name):
            continue
        body = page.locator("#panelbody").inner_text()
        # The card prints the protocol name once, in a row labelled 协议名.
        body = "\n".join(l for l in body.splitlines() if l.strip() != name)
        for w in BANNED_ON_CARDS:
            if w in body:
                bad.setdefault(w, []).append(name)
    check(not bad, "no card prints a protocol word at a person",
          "; ".join("%s on %s" % (w, ",".join(ps)) for w, ps in sorted(bad.items())))


def check_run_one_target(page):
    """The 「单独跑」 button next to a single pt.run target.

    The one-button test runs a port's targets too, so this button was never
    pressed by anything - and it is the one somebody reaches for when they want
    just the SD speed figure or just the PHY probe, without driving the rest of
    the port.
    """
    Section("running one target on its own")

    if not check(pick_port(page, "eth"), "eth is in the port list"):
        return
    rows = page.locator("#panelbody .tgt")
    if not check(rows.count() >= 1, "eth offers a target that can be pressed alone",
                 "%d rows" % rows.count()):
        return
    name = rows.first.locator(".n").inner_text().strip()
    check(name == "以太网 PHY 探测", "the target is named in plain Chinese", name)

    page.fill("#raw", "")           # so the log assertion below cannot match an echo
    rows.first.locator("button").click()
    page.wait_for_timeout(3000)

    stat = page.locator('.prow[data-port="eth"] .stat').inner_text().strip()
    check(stat != "未测", "pressing it alone reaches a verdict", stat)
    log = page.locator("#log").inner_text()
    check("pt.run eth.link" in log,
          "and the command that went out is the one the row names",
          " ".join(log.split())[-160:])


def check_autoecho_toggle(page):
    """The 自动回环应答 box in the header.

    Only the API had ever been exercised. The box is what a bench turns off
    when a second tool wants the control port, and a box that silently does
    nothing would look exactly like a board that stopped answering.
    """
    Section("the auto-echo box")

    box = page.locator("#autoecho")
    if not check(box.count() == 1, "the box is there once connected"):
        return
    was = box.is_checked()
    box.set_checked(not was)
    page.wait_for_timeout(600)
    check(box.is_checked() != was, "it toggles")
    stat = page.locator("#echostat").inner_text().strip()
    check(stat != "", "and says what state it is in now", stat)
    box.set_checked(was)
    page.wait_for_timeout(600)
    check(box.is_checked() == was, "and toggles back")


def check_peer_binding(page):
    """The 绑上 / 解开 pair on a loop=link port.

    ⚠️ What this can and cannot prove on the simulated board:
      - CAN prove: the control is there, it lists this machine's serial ports,
        binding reaches the server and comes back either bound or with a
        readable reason, and 解开 undoes it.
      - CANNOT prove: that binding the right adapter is what closes the loop.
        The simulated board answers its own link ports (its stimulate() plays
        every peer), so a bench cable is the only thing that can show that.
        Left to a real board on purpose rather than faked here.
    """
    Section("binding a peer serial port")

    if not check(pick_port(page, "rs485"), "rs485 is in the port list"):
        return
    sel = page.locator("#panelbody .chvals select")
    if not check(sel.count() >= 1, "the card offers a peer port to bind"):
        return
    options = sel.first.locator("option")
    n = options.count()
    check(n >= 1, "the list is filled from this machine's serial ports", "%d" % n)
    if n == 0:
        return

    com = options.first.get_attribute("value")
    sel.first.select_option(com)
    page.locator("#panelbody .chvals button", has_text="绑上").first.click()
    page.wait_for_timeout(1500)

    body = page.locator("#panelbody").inner_text()
    msg = page.locator("#panelbody .msg")
    if msg.count() and msg.first.inner_text().strip():
        # A refusal is a legitimate outcome here - the port may be in use by
        # something else on this machine. What matters is that it says so in
        # words rather than leaving the card looking bound.
        said = msg.first.inner_text().strip()
        check("绑上" not in body or "解开" not in body,
              "a refused binding does not leave the card looking bound", said)
        check(said != "", "and the refusal is stated in words", said)
        return

    check("解开" in body, "a bound peer offers to be unbound",
          " ".join(body.split())[:160])
    page.locator("#panelbody .chvals button", has_text="解开").first.click()
    page.wait_for_timeout(1000)
    body = page.locator("#panelbody").inner_text()
    check("绑上" in body, "and unbinding puts the chooser back")


def check_plan_runs_from_the_page(page):
    """Pressing 运行 on the plan tab.

    The plan tab is the production path - one press, every step, one report -
    and nothing in a browser had ever pressed it. Only the HTTP API was
    covered, which cannot see that the button is wired to it or that the
    per-step verdicts land back on the steps.

    bench-smoke is used because it needs nothing but the control cable and has
    no UserConfirm step to stop on.
    """
    Section("running a plan from the plan tab")

    page.locator('.tab[data-tab="plan"]').click()
    page.wait_for_timeout(600)
    buttons = page.locator("#planlist button")
    picked = False
    for i in range(buttons.count()):
        if "bench-smoke" in buttons.nth(i).inner_text():
            buttons.nth(i).click()
            picked = True
            break
    if not check(picked, "bench-smoke.json is offered on the plan tab"):
        return
    page.wait_for_selector("#planbody .plansteps .st")
    steps = page.locator("#planbody .plansteps .st")
    want = steps.count()
    check(want >= 5, "its steps are drawn", "%d steps" % want)

    run = page.locator("#planbody .card .hd button").first
    check(run.inner_text().strip() == "运行", "the run button is the first one",
          run.inner_text())
    run.click()

    # Every step judged, or the run is not finished. bench-smoke on the
    # simulated board is about half a minute; the wait is generous on purpose
    # because a slow run and a broken button look identical until it lands.
    got = 0
    for _ in range(180):
        got = page.locator("#planbody .plansteps .vd").count()
        if got >= want:
            break
        page.wait_for_timeout(1000)
    if not check(got >= want, "pressing 运行 runs every step",
                 "%d of %d steps came back" % (got, want)):
        return

    marks = page.locator("#planbody .plansteps .vd")
    outcomes = [marks.nth(i).inner_text().strip() for i in range(marks.count())]
    check(all(o in ("PASS", "SKIPPED") for o in outcomes),
          "and every step of the smoke plan passes on the simulated board",
          " ".join(outcomes))

    page.locator('.tab[data-tab="manual"]').click()
    page.wait_for_timeout(400)


def check_aout_walk(page):
    """The multi-point analog-output walk, with the meter reading typed in.

    Two things are only decidable in a browser. One is that the prompt asking
    for the meter reading SURVIVES - the panel rebuilds itself on every frame,
    and an input inside that region is destroyed under the person's hands while
    they are still typing. The other is that the verdict uses percent of full
    scale: at the bottom of the range one DAC step is already a large fraction
    of the reading, so a percent-of-reading limit is one this hardware cannot
    meet there.
    """
    Section("AOUT multi-point walk")

    page.locator('.tab[data-tab="manual"]').click()
    page.wait_for_selector(".prow")
    rows = page.locator(".prow")
    picked = False
    for i in range(rows.count()):
        if rows.nth(i).get_attribute("data-port") == "aout":
            rows.nth(i).click()
            page.wait_for_timeout(300)
            picked = True
            break
    if not check(picked, "aout is in the port list"):
        return

    card = page.locator('.card', has_text="模拟输出多点测量")
    if not check(card.count() > 0, "the multi-point card appears under aout"):
        return

    # Two points, so the walk is short but still more than one - one point
    # cannot separate an offset from a gain error, which is why it is a walk.
    boxes = card.locator("input")
    boxes.nth(0).fill("1, 20")
    boxes.nth(1).fill("2")
    page.wait_for_timeout(150)

    card.locator("button.aocalgo").click()

    # The prompt has to come up, and it has to still be there after frames have
    # rebuilt the panel underneath it.
    ask = page.locator("#meterask")
    try:
        ask.wait_for(state="visible", timeout=15000)
    except Exception:
        check(False, "the walk asks for a meter reading", "#meterask never appeared")
        return
    check(True, "the walk stops and asks for a meter reading")

    first_text = page.locator("#meterwhat").inner_text()
    check("mA" in first_text, "the prompt says what the board is putting out", first_text)

    # Sit through several frames. This is the assertion that matters: a prompt
    # built inside #panelbody would have been rebuilt away by now.
    page.wait_for_timeout(2500)
    check(ask.is_visible(), "and it is still there after the panel has redrawn")
    typed = page.locator("#meterval")
    typed.fill("0.900")
    check(typed.input_value() == "0.900",
          "what was typed survives the redraws too", typed.input_value())

    # 0.900 against a 1 mA target is -0.1 mA, which is -0.5% of a 20 mA full
    # scale - outside the 2% asked for? No: it is inside. Deliberately, so the
    # first point passes and the second is the one that fails.
    page.locator("#meterok").click()
    try:
        ask.wait_for(state="visible", timeout=15000)
    except Exception:
        check(False, "it moves on to the second point")
        return
    check(True, "it moves on to the second point")
    page.locator("#meterval").fill("18.0")     # 2 mA low on 20 = -10% FS
    page.locator("#meterok").click()
    page.wait_for_timeout(1500)

    card = page.locator('.card', has_text="模拟输出多点测量")
    body = card.inner_text()
    check("过" in body and "不过" in body,
          "the table shows one point passing and one failing", body[-300:])
    check("% FS" in body,
          "and the verdict is stated against full scale, not against the reading",
          body[-300:])
    check("不算补偿值" in body,
          "the card says out loud that it does not compute a correction yet")


def check_limits_are_readonly(page):
    """The plan tab's limits, and the save that could put one back.

    This is where the limits actually live, and until 2026-09-10 nothing looked
    at it: the only assertion was on the port tab, for an attribute nothing ever
    set, so it held whatever the plan tab did. The plan tab was in fact drawing
    every op/min/max/value/unit as a text box, and saving wrote the lot back -
    the opposite of DECISIONS.md 30 and 34, under a green light.

    Two halves, because a page is only half the protection. What is on screen
    is the operator's side; what the server accepts is everybody else's, and a
    request does not have to come from this page.
    """
    import json

    Section("limits are read-only, and unsavable")

    page.locator('.tab[data-tab="plan"]').click()
    page.wait_for_selector("#planlist button")
    plans = page.locator("#planlist button")
    name = plans.nth(0).inner_text().strip()
    plans.nth(0).click()
    page.wait_for_selector("#planbody .plansteps .st")

    # The first step that has limits at all. A step without them would make
    # every assertion below vacuously true.
    steps = page.locator("#planbody .plansteps .st")
    shown = False
    for i in range(steps.count()):
        steps.nth(i).click()
        page.wait_for_timeout(120)
        if page.locator("#planbody .pgrid .ro").count() > 0:
            shown = True
            break
    if not check(shown, "a step's limits are drawn on the plan tab"):
        return

    # Everything between the 判据 heading and the next one belongs to the
    # limits. Counted in the page rather than by selector because the grid is
    # flat - the heading is the only boundary there is.
    editable = page.evaluate(
        """() => {
            const g = document.querySelector('#planbody .pgrid');
            let inside = false, n = 0;
            for (const el of g.children) {
              if (el.classList.contains('pgroup')) { inside = el.textContent.includes('判据'); continue; }
              if (inside) n += el.querySelectorAll('input,select,textarea').length;
            }
            return n;
        }"""
    )
    check(editable == 0,
          "not one limit on the plan tab is typeable", "%d editable" % editable)

    # And the server refuses one anyway. Sent the way anything else would send
    # it, not through the page: the page is what is being taken out of the
    # trust chain here.
    # Absolute: the request context has no page to resolve a relative URL
    # against, whatever the page happens to be showing.
    base = page.url.split("#")[0].rstrip("/")
    got = page.request.get(base + "/api/plan?name=" + name).json()
    if not check("plan" in got, "the plan loads over the API", json.dumps(got)[:200]):
        return

    # The bytes, kept so this run leaves the shipped plan exactly as it found
    # it. Saving through the panel reflows the file and quotes its numbers -
    # harmless to the loader, and still not something a test should leave in a
    # file somebody else's run will diff.
    on_disk = panel_exe().parent / "plans" / name
    original = on_disk.read_bytes() if on_disk.exists() else None
    plan = got["plan"]
    idx = next((i for i, s in enumerate(plan["steps"]) if s.get("checks")), None)
    if not check(idx is not None, "the plan has a step with limits"):
        return

    was_checks = json.loads(json.dumps(plan["steps"][idx]["checks"]))
    was_version = plan["limit_version"]
    was_timeout = plan["steps"][idx].get("timeout_ms")

    # A parameter to move as well, on whichever step has one. Without a
    # legitimate edit in the same request, a save that refused everything
    # outright would pass every assertion below.
    pidx = next((i for i, s in enumerate(plan["steps"]) if s.get("params")), None)
    pkey = pval = None
    if pidx is not None:
        pkey = sorted(plan["steps"][pidx]["params"])[0]
        pval = str(plan["steps"][pidx]["params"][pkey])
        plan["steps"][pidx]["params"][pkey] = pval + "0"

    plan["steps"][idx]["checks"][0]["value"] = "ANYTHING"
    plan["steps"][idx]["checks"][0]["op"] = "contains"
    plan["limit_version"] = "forged"
    plan["steps"][idx]["timeout_ms"] = (was_timeout or 20000) + 1234

    saved = page.request.post(base + "/api/plan", data={"name": name, "plan": plan}).json()
    check("error" not in saved or not saved["error"],
          "the save itself is accepted", json.dumps(saved)[:200])

    back = page.request.get(base + "/api/plan?name=" + name).json()["plan"]
    now = back["steps"][idx]
    check(now["checks"] == was_checks,
          "a widened limit did not survive the save - the file's own limits won",
          json.dumps(now["checks"])[:200])
    check(back["limit_version"] == was_version,
          "and limit_version is still the one the file had",
          "%s -> %s" % (was_version, back["limit_version"]))
    check(now.get("timeout_ms") == (was_timeout or 20000) + 1234,
          "while the parameter edit in the same save did go through",
          str(now.get("timeout_ms")))

    # And the log says which parameter moved and what it moved from. The file
    # only shows where it ended up; which readings were taken before the change
    # is what the log answers and the file cannot.
    if pidx is not None:
        want = "%s %s %s -> %s0" % (plan["steps"][pidx]["id"], pkey, pval, pval)
        page.wait_for_timeout(400)
        check(want in page.locator("#log").inner_text(),
              "and the log names the parameter that moved, and what it moved from",
              want)

    # Put the file back byte for byte, not by saving it again: a second save
    # would leave the reflow behind, which is the thing being avoided.
    if original is not None:
        on_disk.write_bytes(original)
    check(original is None or on_disk.read_bytes() == original,
          "and the plan file is left exactly as it was found")

    page.locator('.tab[data-tab="manual"]').click()


def check_remembered(saved, com):
    Section("what it remembered")
    import json

    if com.strip().lower() == "sim":
        # The opposite assertion, and the one that matters more.
        #
        # The simulated board must NOT be written down: it is not an adapter
        # anybody wired up, and recording it overwrites the answer to a
        # question only a person at the bench can give. That is not
        # hypothetical - a run of this test against sim on 2026-09-08 replaced
        # a bench's rs232/rs485/can mapping with {"control":"sim"}, and the
        # mapping had to be rebuilt by hand.
        if not saved.exists():
            check(True, "nothing was written down for the simulated board")
            return
        got = json.loads(saved.read_text(encoding="utf-8"))
        check(got.get("control", "").lower() != "sim",
              "the simulated board is not written down as a control port",
              json.dumps(got, ensure_ascii=False))
        check(got.get("peers") is not None,
              "and whatever a bench had bound is left alone",
              json.dumps(got, ensure_ascii=False))
        return

    # Asked for on 2026-09-08: work out which adapter is which once, not every
    # time the panel is opened.
    if check(saved.exists(), "the control port is written down after connecting",
             str(saved)):
        got = json.loads(saved.read_text(encoding="utf-8"))
        check(got.get("control") == com,
              "the port written down is the one that was used",
              json.dumps(got, ensure_ascii=False))


def run_checks(page, com):
    # ---------------------------------------------------- before connecting
    Section("before a control port is chosen")
    page.wait_for_selector("#portlist .p")
    rows = page.locator("#portlist .p")
    check(rows.count() > 0, "the serial port list is shown, not hidden in a dropdown",
          "%d rows" % rows.count())
    check(page.locator("#connect").is_disabled(),
          "connect is refused until a port is picked")
    check(page.locator("#portlist .p.on").count() == 0,
          "nothing is preselected on a first run")

    # The whole rest of the page is inert: everything on it is a reading that
    # arrives over the control port, so before that port is open there is
    # nothing on it that could mean anything.
    gate = page.locator("#afterconn")
    check("live" not in (gate.get_attribute("class") or ""),
          "the rest of the page is gated off before connecting")

    # ---------------------------------------------------- pick and connect
    Section("choosing the control port")
    picked = False
    for i in range(rows.count()):
        if com in rows.nth(i).inner_text():
            rows.nth(i).click()
            picked = True
            break
    if not check(picked, "the control port %s is in the list" % com):
        return

    check(not page.locator("#connect").is_disabled(),
          "connect becomes available once a port is picked")
    check(com in page.locator("#connhint").inner_text(),
          "the hint names the port that was picked")

    page.locator("#connect").click()
    page.wait_for_selector("#afterconn.live")
    check(True, "connecting opens the rest of the page")
    status = page.locator("#status").inner_text()
    check("porttool" in status.lower() or re.search(r"\d+\.\d+\.\d+", status),
          "the header shows the firmware version the board reported", status)

    # ---------------------------------------------------- the port list
    Section("the port list the board reported")
    page.wait_for_selector(".prow")
    heads = page.locator(".boardhd")
    labels = [heads.nth(i).inner_text() for i in range(heads.count())]
    check(heads.count() >= 3, "ports are grouped by which board they are on",
          str(labels))
    for want in ("Bridge", "Upper Deck", "Lower Deck"):
        check(any(want in l for l in labels), "there is a %s group" % want, str(labels))

    prows = page.locator(".prow")
    n = prows.count()
    check(n >= 15, "every port the board reported has a row", "%d rows" % n)

    # Untested is the honest starting state, and it has to be visible: a port
    # that looks the same tested and untested is a port somebody will skip.
    first = prows.nth(0).inner_text()
    check("未测" in first, "a port starts as untested", first)

    # ---------------------------------------------------- clicking each port
    Section("clicking every port row")
    for i in range(n):
        name = prows.nth(i).locator(".nm").inner_text()
        prows.nth(i).click()
        cls = prows.nth(i).get_attribute("class") or ""
        if not check("on" in cls.split(), "%s selects when clicked" % name, cls):
            continue
        body = page.locator("#panelbody").inner_text()
        check(name in body,
              "%s selected shows that port in the middle column" % name,
              body[:80].replace("\n", " "))

    # -------------------------------------------- the one button that matters
    #
    # The whole page exists so somebody can pick a port, press one button and
    # read a verdict. This presses it on EVERY port and checks the verdict is
    # the right one - not merely that some verdict appeared.
    #
    # Asserting only "a verdict showed up" is what let a real bug through on
    # 2026-09-08: sdram reported failed because the host's 3 s command timeout
    # was shorter than the 6.5 s sweep, and a weaker check called that a pass.
    #
    # EXPECT says what this bench should produce. A port that needs wiring this
    # bench does not have is expected to FAIL, and the reason is checked too -
    # "it failed" is not good enough when the point is that it failed for the
    # right reason.
    Section("one port, one press - every port")

    EXPECT = {
        # Need nothing wired. These must pass, and a failure here is a real one.
        "sdram": ("pass", None),
        "temp":  ("pass", None),
        "rtc":   ("pass", None),
        "led":   ("pass", None),
        "can":   ("pass", None),      # mode=extloop needs no peer
        "dout":  ("pass", None),      # drives open terminals, judged on drive only
        "relay": ("pass", None),      # ditto, no contact read-back to judge
        # Need something this bench has not got. Expected to fail, for a
        # reason that names the missing thing.
        # detected=0 when the slot is empty, mounted=0 when the card is
        # there but exFAT. Either is a fail; the reason names which.
        "sd":    ("fail", None),
        "din":   ("fail", "v"),         # nothing is driving the inputs
        "ain":   ("fail", "ch1"),       # no signal source on D12/D13
        "rs485": ("fail", "miss"),      # no peer bound on C09/C10
        # eth's session half needs a TCP peer to connect, and this bench has
        # none: the panel does not open sockets, and the production answerer
        # that would (PRODUCTION-TEST-GAP.md, "Golden endpoint") is not written
        # yet. So conn stays 0 and the port fails - which is the honest result,
        # named by the reading that is missing. eth.link, the one-shot PHY half,
        # passes on its own; it is the merged verdict that fails.
        # ⚠️ This flips to ("pass", None) the day that answerer exists.
        "eth":   ("fail", "conn"),
        "knx":   ("either", None),      # bus power is the operator's business
        # rs232 got criteria on 2026-09-08 after this sweep found it had none.
        "rs232": ("pass", None),
        "aout":  ("pass", None),        # judged on the DAC value, not current
        # The burn-in, at the one minute the plan starts it with. Slow on
        # purpose and worth the minute: it is the only port whose test ends
        # when the board says done=1 rather than after a few frames.
        "soak":  ("pass", None),
        # One-way handovers. They print prose for a person and take the board
        # with them, so the page offers no button and says 人工判 rather than
        # 未测 - "nobody can judge this from here" is not "nobody has yet".
        "pwm":     ("manual", None),
        "bringup": ("manual", None),
    }

    if com.strip().lower() == "sim":
        # The simulated board is wired by construction: a fixture holds the
        # digital inputs high, the analog inputs sit inside their bands, and
        # its stimulate() plays every link port's peer. So the four ports that
        # fail for want of wiring on a bench have to pass here.
        #
        # This is not the assertion being relaxed - it is the same assertion
        # against a different bench. Left expecting failure, a simulated run
        # would be red whatever the software did, which is the same as not
        # running it at all.
        for k in ("din", "ain", "rs485", "sd"):
            EXPECT[k] = ("pass", None)
        # The simulated board answers its own CDC pipe. On a bench the peer is
        # the COM port the board enumerates as, bound like any other link port.
        EXPECT["usb"] = ("pass", None)
        # Its session half needs a peer holding an address, so station 6 ships
        # that step disabled - which leaves the session unjudged and the port
        # with it. The one-shot half still has to pass.
        EXPECT["eth"] = ("either", None)

    prows = page.locator(".prow")
    tested = []
    for i in range(prows.count()):
        key = prows.nth(i).get_attribute("data-port")
        want, want_why = EXPECT.get(key, ("either", None))

        if not page.locator("#afterconn.live").count():
            Fail("the panel lost the board - stopping instead of timing out "
                 "on every remaining port")
            failures.append("connection lost mid-sweep")
            return

        print("  ... %s" % key, flush=True)
        prows.nth(i).click()
        btn = page.locator(".startrow button")
        if btn.count() == 0:
            check(want == "manual",
                  "%s offers no start button - it can only be judged by eye" % key)
            check(page.locator(".prow.on .stat").inner_text().strip() == "人工判",
                  "%s says 人工判 rather than 未测" % key,
                  page.locator(".prow.on .stat").inner_text())
            continue

        btn.first.click()

        # A test that runs for minutes has to say how far it has got, or a
        # person cannot tell it apart from one that has hung. The board's own
        # countdown is what the page shows.
        if key == "soak":
            page.wait_for_function(
                "() => { const s = document.querySelector('.startrow .note');"
                " return s && s.textContent.indexOf('还剩') >= 0; }",
                timeout=25000)
            Ok("PASS  soak shows the board's own countdown while it runs")
            check(page.locator(".startrow button").nth(1).inner_text().strip() == "中止",
                  "a long run can be stopped from where it was started")

        # The burn-in times itself; everything else answers in seconds.
        budget = 150000 if key == "soak" else 90000
        page.wait_for_function(
            "() => { const b = document.querySelector('.startrow button');"
            " return b && b.textContent.indexOf('跑着') < 0; }", timeout=budget)

        note = page.locator(".startrow .sub").first.inner_text().strip()
        status = page.locator(".prow.on .stat").inner_text().strip()
        tested.append((key, status, note))

        if want == "pass":
            check(status == "通过",
                  "%s passes on this bench" % key, "%s / %s" % (status, note))
        elif want == "fail":
            ok = check(status == "失败",
                       "%s fails on this bench, as it should" % key,
                       "%s / %s" % (status, note))
            if ok and want_why:
                check(want_why in note,
                      "%s says which reading was wrong" % key, note)
        elif want == "manual":
            pass                        # settled above, before the button

        else:
            check(status in ("通过", "失败"),
                  "%s reaches a verdict either way" % key,
                  "%s / %s" % (status, note))

    Section("what the bench produced")
    for key, status, note in tested:
        print("  %-9s %-6s %s" % (key, status, note[:70]))

    # The one-button test has to start each port the way the plan starts it,
    # including the per-channel parameters. Checked against what actually went
    # down the wire, because getting this wrong does not look like a bug: the
    # board answers happily, having driven nothing, and a healthy board is
    # reported as failed. That is what happened on 2026-09-08 - the panel
    # prefilled the scalar parameters from the plan and left on=/mv=/duty= at
    # the board's defaults of zero.
    Section("the plan's parameters really got sent")
    sent = page.locator("#log").inner_text()
    for want, why in (
            ("on=1:1", "relay is told to close its contacts, not just to hold"),
            ("mv=1:1000", "the analog outputs are told which voltage to produce"),
            ("duty=1:100", "the high-side outputs are told to drive"),
            ("minutes=1", "the burn-in is started with the plan's duration"),
            ("mode=extloop", "can is started in the mode its criteria assume")):
        check(want in sent, "%s (%s)" % (want, why))

    # ---------------------------------------------------- the tabs
    Section("the tabs")
    page.locator('.tab[data-tab="plan"]').click()
    # The list is filled by a fetch, so waiting for a row rather than asserting
    # straight after the click. Asserting immediately is what made this fail
    # the first time - a test bug, not a panel bug.
    page.wait_for_selector("#planlist button")
    check(page.locator("#planlist").is_visible(),
          "the plan tab shows the plan list")
    check(page.locator("#planwhat").is_visible(),
          "the plan tab says in one line what a plan is for")
    plans = page.locator("#planlist button")
    check(plans.count() >= 1, "the plans shipped beside the exe are listed",
          "%d plans" % plans.count())

    # Clicking a plan has to put it in the middle column, the same column a
    # port uses - one rule for that column, whatever is selected.
    plans.nth(0).click()
    page.wait_for_timeout(400)
    check(page.locator("#planbody").inner_text().strip() != "",
          "a plan selected shows its steps in the middle column")
    check(not page.locator("#treebody").is_visible(),
          "the port list is put away while the plan tab is open")

    page.locator('.tab[data-tab="manual"]').click()
    check(page.locator("#treebody").is_visible(), "the port tab comes back")

    # ---------------------------------------------------- the log controls
    Section("the log pane")
    page.locator("#pause").click()
    check(page.locator("#paused").is_visible(),
          "pausing the log says so - it pauses drawing, not reading")
    page.locator("#pause").click()
    check(not page.locator("#paused").is_visible(), "resuming clears that")

    page.locator("#clear").click()
    check(page.locator("#log").inner_text().strip() == "" or True,
          "clearing the log does not throw")

    for box in page.locator(".filt").all():
        box.uncheck()
        box.check()
    check(True, "every log filter can be switched off and on")

    # Exporting the log. Asked for on 2026-09-08: it has to come out as .txt,
    # because that is what opens by double-clicking and what a mail client will
    # accept as an attachment.
    try:
        with page.expect_download(timeout=10000) as dl:
            page.click("#save")
        got = dl.value
        name = got.suggested_filename
        check(name.endswith(".txt"), "the log exports as .txt", name)
        saved = Path(tempfile.gettempdir()) / "porttool_export_check.txt"
        got.save_as(str(saved))
        body = saved.read_text(encoding="utf-8", errors="replace")
        check(body.startswith("PortTool log"),
              "the exported file says what it is on the first line",
              body.splitlines()[0] if body else "(empty)")
        # A log exported to send somebody has to carry which port and which
        # firmware produced it, or it cannot be matched to a board later.
        check("control port:" in body and "firmware:" in body,
              "the export records the port and the firmware it came from")
        check(len(body.splitlines()) > 4,
              "the export has the log lines under that header",
              "%d lines" % len(body.splitlines()))
    except Exception as exc:                        # noqa: BLE001
        Fail("FAIL  exporting the log threw: %s" % str(exc).splitlines()[0])
        failures.append("log export")

    # -------------------------------------------------- which limits judged
    #
    # A tick on this page means nothing unless it says which limit set produced
    # it: the same board passes under one and fails under another. And limits
    # are switched by choosing a whole named plan, never by editing a number on
    # screen - the production guide bans passing a board by widening a limit,
    # and an edited box leaves no trace, while a plan name and its
    # limit_version go into every report.
    Section("which limits the verdicts came from")

    crit = page.locator("#critbox")
    check(crit.count() > 0 and crit.is_visible(),
          "the page says which limits it is judging by, in the header")
    sel = page.locator("#critplan")
    strict = sel.input_value()
    check(strict.endswith(".json"), "a plan file supplies the limits", strict)
    ver_before = page.locator("#critver").inner_text()
    check("限值" in ver_before,
          "and shows that plan's limit_version, not just its name", ver_before)

    options = sel.locator("option").all_text_contents()
    relaxed = next((o for o in options if "relaxed" in o), None)
    if check(relaxed is not None,
             "a relaxed limit set is offered next to the strict one",
             ", ".join(options)):
        # Give the page a verdict to lose, so "switching clears them" is
        # actually observable rather than vacuously true.
        page.fill("#raw", "pt.run rtc.read")
        page.click("#send")
        page.wait_for_timeout(600)

        sel.select_option(relaxed)
        page.wait_for_timeout(800)
        check(sel.input_value() == relaxed,
              "choosing the relaxed set switches to it", sel.input_value())
        ver_after = page.locator("#critver").inner_text()
        check(ver_after != ver_before,
              "the limit_version on screen changes with it",
              "%s -> %s" % (ver_before, ver_after))
        check(page.locator(".prow .stat", has_text="通过").count() == 0,
              "the verdicts from the old limits are cleared, not left looking current")
        log = page.locator("#log").inner_text()
        check("判据换成" in log,
              "and the log records the change, so a report can be traced to it")

        sel.select_option(strict)
        page.wait_for_timeout(600)
        check(sel.input_value() == strict, "and it switches back")

    # No editable limit anywhere: that is the property being protected.
    check(page.locator("#panelbody input[data-limit]").count() == 0,
          "no limit is editable on the port tab")

    check_limits_are_readonly(page)
    check_channel_labels(page)
    check_off_plan_params(page)
    check_nothing_in_english(page)
    check_run_one_target(page)
    check_autoecho_toggle(page)
    check_peer_binding(page)
    check_plan_runs_from_the_page(page)
    check_aout_walk(page)

    # -------------------------------------------------- DO -> DI cross-check
    #
    # One eight-way cable, sixteen channels. What matters is not that it can
    # report a good cable - it is that it finds a crossed pair, which is the
    # failure neither port can see on its own. So this drives the simulated
    # board's cable into each of its three states and checks the panel says the
    # right thing about each.
    #
    # Only on the simulated board: making a real bench cross two wires and then
    # uncross them is not something a test can ask of a person.
    if com.strip().lower() == "sim":
        Section("DO -> DI cross-check")

        def raw(cmd):
            page.fill("#raw", cmd)
            page.click("#send")
            page.wait_for_timeout(250)

        def pick(name):
            prows = page.locator(".prow")
            for i in range(prows.count()):
                if prows.nth(i).get_attribute("data-port") == name:
                    prows.nth(i).click()
                    return True
            return False

        if not check(pick("dout"), "dout is in the port list"):
            pass
        else:
            card = page.locator(".card", has=page.locator("text=DO → DI 对照"))
            check(card.count() > 0,
                  "the cross-check appears under dout - the cable joins two ports")
            check(pick("din"), "din is in the port list too")
            check(page.locator(".card", has=page.locator("text=DO → DI 对照")).count() > 0,
                  "and under din as well, so it is reachable from either end")

            pick("dout")

            def walk_cable(mode, label, want_ok, want_word):
                raw("sim.cable %d" % mode)
                btn = page.locator("button.dodigo")
                if btn.count() == 0:
                    Fail("no 走一遍 button on the cross-check card (%s)" % label)
                    failures.append("cross-check button missing")
                    return
                btn.first.click()
                # Eight outputs, two settle frames each at 200 ms.
                # Its own class, because the panel rebuilds the whole pane
                # after every command: waiting on "the first button" would
                # watch the session card's 启动 and return immediately.
                page.wait_for_function(
                    "() => { const b = document.querySelector('button.dodigo');"
                    " return b && !b.disabled; }",
                    timeout=120000)
                said = page.locator(".sub2").first.inner_text()
                if want_ok:
                    check(said.startswith("✅"),
                          "a good cable reads as passing (%s)" % label, said)
                else:
                    ok = check(said.startswith("❌"),
                               "%s is caught, not passed" % label, said)
                    if ok:
                        check(want_word in said,
                              "and it says which wire to look at (%s)" % want_word, said)

            walk_cable(1, "straight through", True, None)
            walk_cable(2, "DO1 and DO2 swapped", False, "错位")
            walk_cable(3, "DO3 open", False, "断了")

            # Leave the board the way the rest of the run expects it.
            raw("sim.cable 0")
            raw("pt.stop all")

    # ---------------------------------------------------- KNX frame mode
    #
    # The point of mode=frames is that a person can put a real KNX frame on the
    # bus from this page and read back what the bus said - and that the page
    # answers "which reading of the octets is the real frame" with the check
    # octet rather than leaving it to the eye.
    #
    # ⚠️ This transmits on the installation, to group address 31/7/255. That
    # address is the default because it is the corner of the address space a
    # real project is least likely to have assigned; nothing subscribes to it.
    Section("KNX frame mode")

    def pick_port(name):
        rows = page.locator(".prow")
        for i in range(rows.count()):
            if rows.nth(i).get_attribute("data-port") == name:
                rows.nth(i).click()
                page.wait_for_timeout(200)
                return True
        return False

    if not check(pick_port("knx"), "knx is in the port list"):
        pass
    else:
        # The address controls exist because the board advertised them as
        # parameters - nothing about knx is written into the page.
        # A parameter is either a typed field (in .row) or a set of choices
        # (its own block). Both carry data-param, so this does not care which.
        holders = page.locator("#panelbody [data-param]")
        names = [holders.nth(i).get_attribute("data-param")
                 for i in range(holders.count())]
        for want in ("ga", "src", "val"):
            check(want in names,
                  "the card offers %s, straight from what the board accepts" % want,
                  " ".join(n for n in names if n))

        # val is advertised as a set of values, so it is offered as choices -
        # a typo in a field would be refused by the board after the fact.
        val_ctl = page.locator('#panelbody [data-param="val"] input[type=radio]')
        check(val_ctl.count() >= 2,
              "val is a set of choices, because the board declares it as one",
              "%d choices" % val_ctl.count())
        # ga is NOT: an address is neither a range nor a set, so it must stay
        # typeable. A vertical bar in its limits line would have made the panel
        # offer the format words as choices instead of an address field.
        ga_sel = page.locator('.card .row label[data-param="ga"]').locator('select')
        check(ga_sel.count() == 0, "ga stays typeable - an address is not a menu")

        page.fill("#raw", "pt.start knx mode=frames period=1000")
        page.click("#send")
        # A frame goes out once a period, comes back over the bus, and the peer
        # acknowledges it. Twenty-five seconds is room for several.
        #
        # ⚠️ Waited for on the rendered node, NOT on document.body.textContent.
        # That includes the <script> source, and this page's own source carries
        # the words 报文事件 and crc=raw inside a hint string - so a wait on the
        # body returns instantly, before a single frame has arrived, and every
        # assertion after it reads the page's source instead of its render.
        # That is exactly what happened while writing this check.
        box = page.locator(".card .chvals", has_text="报文事件")
        try:
            box.first.wait_for(timeout=25000)
        except Exception as e:
            Fail("no frame events on the knx card: %s" % type(e).__name__)
            failures.append("knx frame events missing")
        else:
            Ok("PASS  received frames appear on the card as events")
            text = box.first.inner_text()
            seen = " ".join(text.split())[:180]
            check("原样就对" in text or "取反才对" in text,
                  "the card says which reading of the octets passed the check octet",
                  seen)
            check("单字节应答" in text,
                  "a lone acknowledge octet reads as ack, not as a bad frame", seen)
            check("目标组地址 31/7/255" in text,
                  "and which group address the frame carried", seen)
        page.fill("#raw", "pt.stop knx")
        page.click("#send")
        page.wait_for_timeout(400)

    # ---------------------------------------------------- disconnect
    Section("disconnecting")
    page.locator("#disconnect").click()
    page.wait_for_selector("#afterconn:not(.live)")
    check(True, "disconnecting gates the page again")


if __name__ == "__main__":
    sys.exit(main())
