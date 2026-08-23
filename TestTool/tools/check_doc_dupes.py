"""One fact, one file: fail if the same claim is written out in two documents.

Case P8. Added 2026-08-22, when the user made "the same thing must be described
in exactly one file" a hard requirement. Thirty-odd documents is past the point
where that can be held by eye -- the 2026-08-22 sweep found nineteen duplicated
blocks, and three numbers that had drifted apart precisely because two files both
claimed them.

How it decides. Every document is split into sentences, markdown emphasis is
stripped so a bolded copy matches a plain one, and any sentence long enough to be
a claim that appears in two or more files is a failure. A pointer ("see X") is
short and generic, so pointing is never flagged -- which is the whole point: the
fix for a duplicate is a pointer.

Fenced code blocks are reported separately and do NOT fail the run. Captured
serial output, log lines and shell commands get quoted in more than one place for
good reason: a release note has to show the customer the exact string they will
see, and an acceptance record has to show what the board actually printed. Those
quote the firmware, not each other -- the source of truth is the .c file.

    python tools/check_doc_dupes.py            report; exit 1 on prose duplicates
    python tools/check_doc_dupes.py --min 40   only longer claims
    python tools/check_doc_dupes.py --code     also list the code-block ones

Exit 0 = no prose claim appears twice, 1 = at least one does, 2 = setup problem.
"""

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import Fail, Ok, Section, Warn, cfg  # noqa: E402

TESTTOOL = HERE.parent

# Markdown noise: two copies of one claim rarely agree on bolding or punctuation.
NOISE = re.compile(r"[*`>#\[\]()|~—\-\s。，、：；！？…“”\"'‘’]+")
SPLIT = re.compile(r"[。！？\n]")
FENCE = re.compile(r"^\s*```")

# Deliberate exceptions. Each needs a reason, because "it is special" is how real
# drift hides. Keyed on a distinctive fragment of the normalised sentence.
ALLOWED = {
    "TestToolTEST-CASESmd": "the pointer target itself; naming it is not a claim",
}


def docs():
    """Every prose document in the product, both repos."""
    boot = Path(cfg.BOOT_REPO)
    out = []
    for p in [boot / "CLAUDE.md", boot / "RELEASE-NOTES.md", boot / "OpenPLC_Bootloader.md",
              TESTTOOL / "TEST-CASES.md", TESTTOOL / "acceptance" / "checklist.md",
              TESTTOOL.parent / "CLAUDE.md"]:
        if p.is_file():
            out.append(p)
    d = boot / "docs"
    if d.is_dir():
        for dp, dirs, fs in os.walk(d):
            # artifacts/ holds rendered snapshots of docs -- they are copies by
            # definition and say so in their own index.
            dirs[:] = [x for x in dirs if x not in {".git", "artifacts", "__pycache__"}]
            out.extend(Path(dp) / f for f in fs if f.endswith(".md"))
    return sorted(set(out))


def scan(path, min_len):
    """Yield (normalised, raw, lineno, in_code) for each claim-sized sentence."""
    in_code = False
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if FENCE.match(line):
                in_code = not in_code
                continue
            if line.lstrip().startswith("|") and line.count("|") > 3:
                parts = re.split(r"\|", line)
            else:
                parts = SPLIT.split(line)
            for raw in parts:
                n = NOISE.sub("", raw)
                if len(n) >= min_len:
                    yield n, raw.strip(), lineno, in_code


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--min", type=int, default=28,
                    help="minimum normalised length to count as a claim")
    ap.add_argument("--code", action="store_true",
                    help="also list duplicates inside fenced code blocks")
    args = ap.parse_args()

    Section("one fact, one file")
    files = docs()
    if not files:
        Fail("no documents found -- is BOOT_REPO set in config/machine.py?")
        return 2
    print("  %d document(s), claim length >= %d" % (len(files), args.min))

    prose, code = defaultdict(list), defaultdict(list)
    for p in files:
        for n, raw, lineno, in_code in scan(p, args.min):
            (code if in_code else prose)[n].append((p, lineno, raw))

    def dups(table):
        out = []
        for n, hits in table.items():
            if len({h[0] for h in hits}) > 1:
                if any(k in n for k in ALLOWED):
                    continue
                out.append((n, hits))
        return sorted(out, key=lambda t: -len({h[0] for h in t[1]}))

    bad = dups(prose)
    meh = dups(code)

    if meh:
        Section("in code blocks -- quoted output and commands, not failures")
        if args.code:
            for n, hits in meh:
                print("  %s" % hits[0][2][:96])
                for p, lineno, _ in hits:
                    print("      %s:%d" % (p, lineno))
        else:
            print("  %d block(s); --code to list them" % len(meh))
        print("  a release note and an acceptance record may both quote the same")
        print("  firmware string -- they quote the .c file, not each other")

    Section("result")
    if bad:
        for n, hits in bad:
            Fail("  %s" % hits[0][2][:96])
            for p, lineno, _ in hits:
                print("        %s:%d" % (p, lineno))
        print("")
        Fail("%d claim(s) written out in more than one file" % len(bad))
        Warn("fix by keeping ONE home and making the others a pointer to it")
        return 1

    Ok("no claim is written out in more than one file")
    return 0


if __name__ == "__main__":
    sys.exit(main())
