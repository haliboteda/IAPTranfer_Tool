"""No renamed id may still appear anywhere.

Reads the old->new table produced by the renumbering effort and fails if any
old id is still cited anywhere in the product. A citation is an id carrying a
marker -- an introducing word, bold, or a table cell of its own. A bare token
is not: C1 and E8 occur all over C code and pin tables. This is the only thing that
can catch a missed rename: P7 compares two name lists with each other and P9
checks that paths resolve, so an id left behind in prose or in a source comment
is invisible to both.

    python3 tools/check_no_stale_ids.py            # judge, exit 1 on a hit
    python3 tools/check_no_stale_ids.py --list     # print the ids it is looking for

Exit codes: 0 no old id survives, 1 at least one does, 2 the table is missing.

Not part of selfcheck until the renumbering is actually done -- before that it
would report the ids that have not been changed yet, which is not a defect.
"""

import argparse
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (cfg, Section, Ok, Fail, Warn, read_text)  # noqa: E402

# The mapping is the single source of what got renamed. Nothing is hardcoded
# here, so adding a rename to the table is all it takes to have it enforced.
TABLES = ("maps/docs-restructure/DR-02-id-mapping.md",       # requirement ids
          "maps/docs-restructure/DR-02-test-id-mapping.md")  # test case ids

# Where an id-shaped string is legitimately something else. Kept narrow and
# justified one by one, because a broad exclusion silently stops enforcing.
ALLOW = {
    # SHA-256's own working variables are literally called T1, T2, S0, S1.
    "sha256.c", "sha256_ref.py", "sha256.h",
    # The map that records the renaming, and the effort's own tickets, quote the
    # old ids on purpose -- that is what they are for.
    "DR-02-id-mapping.md", "DR-02-test-id-mapping.md",
    "ID-MIGRATION.md", "ID-MAP.md",
    # P6 names the selfcheck step here, which DR-10 keeps, not M2's T2-06.
    "RELEASE-NOTES.md",
    # Terminal-block labels collide exactly with the old ids: relays on
    # B01+B02..B11+B12, RS485 on C10/C11, ground on C02/C11/C12, low-side
    # gates on Lower Deck T2-T7, and PE2 -> terminal A2. Every file here was
    # read once; what is excluded is hardware naming, not unread files.
    "HARDWARE-FACTS.md", "FIXTURE-INTERFACE.md", "PORTTOOL-FLOW.md",
    "BOARD-BRINGUP-CASES.md", "PROD-CONFIG-ITEMS.md", "PRODUCTION-FRAMEWORK.md",
    # Uses B12 as the example of a silkscreen name two ports both claim.
    "CONVENTIONS.md",
    # "any C11 compiler" -- the language standard, not requirement C11.
    "HOST-C-TESTS.md",
    # G1/G2/G3 here are owner-record generations, not the staging case.
    "M2-ownership.md",
    # This file quotes the ambiguous tokens in its own ALLOW comments.
    "check_no_stale_ids.py",
    # A dated run record. It quotes the board's own log verbatim, and the
    # sketch that was installed prints "[M5] ready" -- a sketch name taken
    # from the old case id, not a document citing one. Quoting the log is
    # the evidence; rewording it would be rewriting what the board said.
    "2026-09-18-boot-iap-full-run.md",
    # Third-party OpenAMP sources; A7 is a register field there.
    "mbox_ipcc.c", "mbox_ipcc_template.c",
}
# maps/ is the record of how each decision was reached. Citing the id that was
# current when a ticket was written is correct there, so it is not scanned.
ALLOW_DIRS = {".git", "node_modules", "__pycache__", ".venv", "artifacts",
              ".scratch", "maps",
              # build products, regenerated from sources that are scanned
              "Output",
              # Vendor trees: their docs carry tokens like "flash=C8" and
              # "F1|l4", and nothing in them is ours to rename.
              "Middlewares", "CMSIS", "CI"}

SCAN_EXT = {".md", ".go", ".c", ".h", ".py", ".ino", ".json", ".sh", ".txt", ".yml", ".ld"}


def old_ids(table_paths):
    """Every old id named in the mapping tables' 老编号 column."""
    ids = set()
    lines = []
    for p in table_paths:
        lines += read_text(p).split("\n")
    for line in lines:
        if not line.startswith("|"):
            continue
        cells = [c.strip().strip("*` ") for c in line.split("|")]
        if len(cells) < 3:
            continue
        cand = cells[2]
        if re.fullmatch(r"[A-Z]{1,3}\d{1,2}[a-z]*(?:-(?:neg|attack))?", cand):
            ids.add(cand)
    return ids


def roots():
    out = []
    # HW_REPO is left out: it is the schematic and terminal-naming authority,
    # so every id-shaped token in it is a Klemmblock label (C10 = RS485 A),
    # never a requirement id.
    for key in ("BOOT_REPO", "CORE_REPO", "DOCS_REPO", "REF_REPO", "PORTTOOL_REPO"):
        p = getattr(cfg, key, "")
        if p and Path(p).is_dir():
            out.append(Path(p))
    tool = Path(__file__).resolve().parent.parent.parent
    if tool.is_dir():
        out.append(tool)
    return out


def patterns(ids):
    """Two patterns: prose is scanned wide, source narrow.

    In a .md a bare id is a reference -- that is how tree annotations and
    headings write them, and those are exactly the ones a marker-only pattern
    missed. In source the same token is usually a variable or a register name,
    so there it still has to carry a marker to count.

    The guards keep T1 from matching inside T1-07, and A1 from matching
    inside the acceptance-checklist id CHK-A1.
    """
    alt = "|".join(sorted(ids, key=len, reverse=True))
    prose = re.compile(r"(?<![-\w\u2013])(%s)(?![-\w\u2013])" % alt)
    source = re.compile(
        r"(?:需求|用例|测试|requirement|case)\s*\*{0,2}(%s)(?![-\w])"
        r"|\*\*(%s)\*\*"
        r"|\|\s*`?(%s)`?\s*\|" % (alt, alt, alt))
    return prose, source


def scan(ids):
    """Yield (path, lineno, id, line) for every surviving old id."""
    if not ids:
        return
    prose, source = patterns(ids)
    seen = set()
    for root in roots():
        for dirpath, dirnames, filenames in os.walk(str(root)):
            dirnames[:] = [d for d in dirnames if d not in ALLOW_DIRS]
            for fn in filenames:
                suffix = Path(fn).suffix
                if suffix not in SCAN_EXT or fn in ALLOW:
                    continue
                p = Path(dirpath) / fn
                if str(p) in seen:
                    continue
                seen.add(str(p))
                try:
                    text = read_text(p)
                except Exception:
                    continue
                pattern = prose if suffix == ".md" else source
                for i, line in enumerate(text.split("\n"), 1):
                    m = pattern.search(line)
                    if m:
                        which = next(g for g in m.groups() if g)
                        yield p, i, which, line.strip()[:110]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true", help="print the ids and stop")
    args = ap.parse_args()

    Section("no renamed id survives")
    tables = [Path(cfg.DOCS_REPO) / rel for rel in TABLES]
    missing = [p for p in tables if not p.is_file()]
    if missing:
        for p in missing:
            Fail("mapping table not found: %s" % p)
        return 2

    ids = old_ids(tables)
    if not ids:
        Fail("no old ids parsed out of the mapping tables -- has their shape changed?")
        return 2

    if args.list:
        print("  %d id(s): %s" % (len(ids), " ".join(sorted(ids))))
        return 0

    hits = list(scan(ids))
    print("  looking for %d renamed id(s) across %d repo(s)" % (len(ids), len(roots())))
    if not hits:
        Ok("no renamed id is still written anywhere")
        return 0

    by_id = {}
    for p, lineno, which, line in hits:
        by_id.setdefault(which, []).append((p, lineno, line))
    for which in sorted(by_id):
        Fail("  %s still appears %d time(s)" % (which, len(by_id[which])))
        for p, lineno, line in by_id[which][:4]:
            print("        %s:%d  %s" % (p, lineno, line))
        if len(by_id[which]) > 4:
            print("        ... and %d more" % (len(by_id[which]) - 4))
    Warn("a hit that is not a stale id means the name is ambiguous -- "
         "add the file to ALLOW with a reason, do not widen the pattern")
    return 1


if __name__ == "__main__":
    sys.exit(main())
