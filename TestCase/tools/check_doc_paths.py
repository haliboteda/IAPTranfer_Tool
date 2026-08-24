"""Every file path a document names has to exist.

Case P9. Added 2026-08-22 after the docs/ reorganisation broke 105 references and
every one of them was found by hand. Two of the places that broke are the worst
possible ones: the /portable:init and /openplc:wrap-up skills, which are what a
new machine and a new session run first -- they misled at the exact moment nobody
yet knew their way around, and nothing would ever have told anyone.

It checks only the three shapes whose base directory is unambiguous:

  * markdown links            [text](../design/OWNERSHIP.md)     -- relative to the doc
  * repo-var paths            $BOOT/docs/work/M7-python-scripts.md
  * backticked docs/ paths    `docs/test/MEASUREMENTS.md`        -- some repo root

$PROD and $PORT name the product-level and machine bring-up documents, which live
in the AI-Skills checkout. They exist because a relative link from a product repo
into AI-Skills is not writable: AI-Skills is shared across projects and is not a
sibling of the six product repos on every machine. So a citation that crosses
that boundary has to be a backticked $PROD/... -- and being a repo-var path, it
gets checked. A missing AI-Skills clone is now a setup failure (exit 2), not a
warning: without it most of the link graph goes unchecked, and a check that goes
green while blind is worse than no check at all.

⚠️ **It deliberately ignores every other backticked path**, and that is the whole
design. The docs write `tools/init_machine.py` and `host/fakeboard/run_cases.py`
meaning "relative to whichever repo this paragraph is about", which a checker
cannot know. The first version guessed, and reported 154 dead references of which
about five were real. A check that cries wolf 150 times is worse than no check --
this project has already paid for that lesson twice: a flaky case that trained
people to re-run it, and a warning everyone learned to ignore. Narrower coverage
that can be trusted beats broader coverage that cannot.

What that costs: a bare `tools/foo.ps1` that gets deleted still goes unnoticed.
Write `$TOOL/TestCase/tools/foo.ps1` when you want it checked.

Line numbers in a path (fmc.c:153-193) are stripped before checking: the file has
to exist, but a line number is a hint and drifts by design -- M4 already carries
the scar of trusting one.

Documents outside the two code repos are checked too. AI-Skills is not a sibling
of the others on every machine, so it is located rather than assumed.

    python tools/check_doc_paths.py           report; exit 1 on a dead reference
    python tools/check_doc_paths.py --list    print every path it resolved

Exit 0 = every named path exists, 1 = at least one does not, 2 = setup problem.
"""

import argparse
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import Fail, Ok, Section, Warn, cfg, port_docs, prod_docs, skills_repo  # noqa: E402

TESTTOOL = HERE.parent

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
# $BOOT/... and $TOOL:... -- the repo-variable convention the docs declare.
#
# $PROD and $PORT joined on 2026-08-24, when the product-level documents moved
# into the AI-Skills checkout. They are not a convenience: AI-Skills is NOT a
# sibling of the product repos on every machine, so a relative markdown link from
# a product repo into it cannot be written at all. A backticked $PROD/... is the
# only citation shape that both survives on another machine and gets checked --
# which means these two names extend this check across a repository boundary
# rather than costing it coverage.
VAR_PATH = re.compile(r"\$(BOOT|TOOL|CORE|PROD|PORT)(?:_REPO)?[:/]([\w./+-]+)")
# `docs/...` in backticks. Only docs/, because that prefix pins the base to a
# repo root -- every other bare path in these documents is relative to whichever
# repo the surrounding paragraph is about, which is not knowable from here.
TICK_PATH = re.compile(r"`(docs/[\w./+-]+\.\w+)`")

SKIP_DIR = {".git", "__pycache__", "Debug", "Release", "node_modules", ".vscode"}
# Placeholders, globs and brace expansions are not claims about a file that
# exists. "machine.{ps1,py}" arrives here truncated at the dot, and "path/to/X"
# is an illustration in a rule about how to write paths.
SKIP_TOKEN = re.compile(r"[<>*?{}]|^https?:|^#|^mailto:|(?:^|/)path/to/|\.$")


def repos():
    boot = Path(cfg.BOOT_REPO)
    tool = TESTTOOL.parent
    core = Path(getattr(cfg, "CORE_REPO", "") or "")
    # Located, not assumed: AI-Skills is shared across projects and does not sit
    # beside the six product repos on every machine. common.skills_repo() reads
    # SKILLS_REPO from config and keeps the old two-candidate probe as fallback,
    # so all three document checks agree on where it is.
    return boot, tool, core, skills_repo()


def docs(boot, tool, core, skills):
    out = []
    for root, subs in ((boot, ["docs", "CLAUDE.md", "RELEASE-NOTES.md", "OpenPLC_Bootloader.md"]),
                       (tool, ["CLAUDE.md", "TestCase/TEST-CASES.md",
                               "TestCase/acceptance/checklist.md"]),
                       (core, ["CLAUDE.md"]),
                       (skills, ["OpenPLC", "Portable", "_shared",
                                 "CLAUDE.md", "README.md"])):
        if root is None or not str(root) or not root.exists():
            continue
        for s in subs:
            p = root / s.replace("/", os.sep)
            if p.is_file():
                out.append((p, root))
            elif p.is_dir():
                for dp, dirs, fs in os.walk(p):
                    dirs[:] = [d for d in dirs if d not in SKIP_DIR]
                    out.extend((Path(dp) / f, root) for f in fs if f.endswith(".md"))
    return sorted(set(out))


def resolve(token, doc, doc_root, boot, tool, core, skills):
    """Where a named path should be, or None if the token is not a claim."""
    if SKIP_TOKEN.search(token):
        return None
    token = token.split("#")[0]
    # fmc.c:153-193 -- the file must exist, the line number is a hint
    token = re.sub(r":\d+(?:-\d+)?$", "", token)
    if not token or token.endswith(("/", ":")):
        return None

    m = VAR_PATH.match(token)
    if m:
        base = {"BOOT": boot, "TOOL": tool, "CORE": core,
                "PROD": prod_docs(), "PORT": port_docs()}[m.group(1)]
        if not base or not str(base):
            return None
        return base / m.group(2).replace("/", os.sep)

    if token.startswith("docs/"):
        # Pinned to a repo root, but which repo depends on the sentence -- so it
        # passes if any repo has it. That is enough to catch a path that moved.
        for base in (boot, tool, core, skills, doc_root):
            if base and str(base) and (base / token.replace("/", os.sep)).exists():
                return base / token.replace("/", os.sep)
        return boot / token.replace("/", os.sep)

    if token.startswith(("./", "../")) or token.endswith(".md"):
        # relative to the document, then to its repo root: docs cite both ways
        for cand in (doc.parent / token, doc_root / token):
            try:
                c = Path(os.path.normpath(str(cand)))
            except (OSError, ValueError):
                continue
            if c.exists():
                return c
        return Path(os.path.normpath(str(doc.parent / token)))
    return None


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--list", action="store_true", dest="list_only")
    args = ap.parse_args()

    Section("every documented path exists")
    boot, tool, core, skills = repos()
    if not boot.exists():
        Fail("BOOT_REPO does not exist -- run tools/init_machine.py")
        return 2
    print("  boot    %s" % boot)
    print("  tool    %s" % tool)
    if skills is None:
        # Until 2026-08-24 this was a Warn and the run still passed. That was
        # right while AI-Skills held two skills; it is wrong now that it holds
        # STATUS.md and the rest of the product-level documents. Without it this
        # check cannot resolve any $PROD/ or $PORT/ citation -- it would go green
        # while blind to most of the link graph, which is the one outcome a check
        # must never produce.
        Fail("SKILLS_REPO is not set and no AI-Skills clone was found.")
        Fail("  $PROD and $PORT cannot be resolved, so most citations go unchecked.")
        Warn("  clone it, then: python tools/init_machine.py --redetect SKILLS_REPO")
        return 2
    print("  prod    %s" % prod_docs())
    print("  port    %s" % port_docs())

    files = docs(boot, tool, core, skills)
    print("  %d document(s)" % len(files))

    dead, checked = [], 0
    for doc, root in files:
        try:
            text = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for rx in (MD_LINK, TICK_PATH):
            for m in rx.finditer(text):
                tok = m.group(1)
                target = resolve(tok, doc, root, boot, tool, core, skills)
                if target is None:
                    continue
                checked += 1
                if not target.exists():
                    lineno = text[:m.start()].count("\n") + 1
                    dead.append((doc, lineno, tok))
        for m in VAR_PATH.finditer(text):
            tok = m.group(0)
            target = resolve(tok, doc, root, boot, tool, core, skills)
            if target is None:
                continue
            checked += 1
            if not target.exists():
                lineno = text[:m.start()].count("\n") + 1
                dead.append((doc, lineno, tok))

    if args.list_only:
        print("  %d path reference(s) resolved" % checked)
        return 0

    Section("result")
    print("  %d path reference(s) checked" % checked)
    if dead:
        for doc, lineno, tok in dead:
            Fail("  %s:%d  ->  %s" % (doc, lineno, tok))
        print("")
        Fail("%d reference(s) name a file that does not exist" % len(dead))
        return 1

    Ok("every path named in a document exists")
    return 0


if __name__ == "__main__":
    sys.exit(main())
