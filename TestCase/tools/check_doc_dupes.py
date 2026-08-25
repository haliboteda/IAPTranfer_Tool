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

What it reads. Every prose document in every repo that holds one: the bootloader
repo and its docs/, this repo, the three sibling repos' CLAUDE.md, and the whole
AI-Skills tree -- the product-level documents, the machine bring-up documents,
every skill body and the standing rules. AI-Skills came inside the walk on
2026-08-24, when the product-level documents moved into it; leaving it outside
would have taken most of the product's prose out of this guarantee silently,
while the check went on exiting 0.

One rule that is deliberately NOT an exception: the same rule stated in English
in a README and in Chinese in a CLAUDE.md does not collide, because the two
normalise differently. That is why the AI-Skills pair needs no entry in ALLOWED.

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

from common import Fail, Ok, Section, Warn, cfg, skills_repo  # noqa: E402

TESTTOOL = HERE.parent

# Markdown noise: two copies of one claim rarely agree on bolding or punctuation.
NOISE = re.compile(r"[*`>#\[\]()|~—\-\s。，、：；！？…“”\"'‘’]+")
SPLIT = re.compile(r"[。！？\n]")
FENCE = re.compile(r"^\s*```")

# Deliberate exceptions. Each needs a reason, because "it is special" is how real
# drift hides. Keyed on a distinctive fragment of the normalised sentence.
#
# Empty since 2026-08-24. The one entry it held had never matched anything: its
# key was written with the hyphens and the dot removed, but NOISE strips '-'
# without stripping '/' or '.', so the normalised sentence still carried both and
# the key was not a substring of it. Emptying the dict changed neither the output
# nor the exit code, which is how we know.
#
# The lesson is about the shape of a key, not about that one document: a key here
# has to be a fragment of the sentence AFTER NOISE runs, so build it by normalising
# a real sentence rather than by hand. An exception that looks like protection and
# is not is worse than none, because the next person to touch that document will
# trust it.
ALLOWED = {
    # The one pointer every product repository carries, by design: each repo's
    # CLAUDE.md names where the product-level documents are. That is the whole
    # point -- a pointer in one repo only would leave the other five silent. It
    # is long enough to read as a claim, so it needs an entry here rather than
    # being shortened until the check stops noticing it.
    "产品全貌<AISkills/OpenPLC/docs/OVERVIEW.md（本机位置见SKILLS_REPO）": "the product-docs pointer; every repo is meant to carry it",
}


SKIP_WALK = {".git", "artifacts", "__pycache__", "node_modules", ".claude"}


def _walk_md(root, out):
    """Every .md under root. artifacts/ holds rendered snapshots of docs -- they
    are copies by definition and say so in their own index."""
    if root is None or not root.is_dir():
        return
    for dp, dirs, fs in os.walk(root):
        dirs[:] = [x for x in dirs if x not in SKIP_WALK]
        out.extend(Path(dp) / f for f in fs if f.endswith(".md"))


def docs():
    """Every prose document in the product, across all the repos that hold one.

    The four CLAUDE.md files outside the bootloader repo were missing until
    2026-08-24, and the gap was not theoretical: open_plc_arduino/CLAUDE.md held
    a second copy of a rule that also lived in the bootloader's docs, and this
    check could not see it. A document outside the walk is a document outside the
    one-fact-one-file guarantee, so the list below has to name every home.
    """
    boot = Path(cfg.BOOT_REPO)
    out = []
    named = [boot / "CLAUDE.md", boot / "RELEASE-NOTES.md", boot / "OpenPLC_Bootloader.md",
             TESTTOOL / "TEST-CASES.md", TESTTOOL / "acceptance" / "checklist.md",
             TESTTOOL.parent / "CLAUDE.md"]
    # The sibling repos' own CLAUDE.md. They state facts about themselves now,
    # which is exactly why a claim leaking between them has to fail here.
    for key in ("CORE_REPO", "HW_REPO", "REF_REPO"):
        root = getattr(cfg, key, "")
        if root:
            named.append(Path(root) / "CLAUDE.md")
    skills = skills_repo()
    if skills:
        named += [skills / "CLAUDE.md", skills / "README.md"]
    out += [p for p in named if p.is_file()]

    _walk_md(boot / "docs", out)
    if skills:
        # The product-level documents ($PROD) and the standing rules. AI-Skills
        # holds product facts from 2026-08-24 on, so it is inside the guard, not
        # beside it -- see its own CLAUDE.md for the placement rule.
        for sub in ("OpenPLC", "_shared"):
            _walk_md(skills / sub, out)
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
