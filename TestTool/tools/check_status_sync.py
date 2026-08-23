"""Case ids in STATUS.md and TEST-CASES.md have to name the same set of cases.

Case P7. Added 2026-08-22, for the gap that docs/test/COVERAGE-GAPS.md had been
admitting for days: "nothing checks whether a conclusion in the docs has gone
stale". This does not close that gap -- it closes the structural half of it.

What it catches:

  * a case that STATUS.md claims as evidence but TEST-CASES.md does not define
    (a requirement pointing at a case nobody can run);
  * a case defined in TEST-CASES.md that no requirement in STATUS.md claims
    (a test whose result nobody records, so nobody notices when it rots);
  * a requirement id referenced from a case row that STATUS.md does not define.

What it does NOT catch: a status that is simply out of date -- "BG1 failed
yesterday but D4 still says PASS". That needs the run result to reach the table
by itself, and today a person types it in. The "evidence still valid?" column in
STATUS.md exists because of exactly that hole.

    python tools/check_status_sync.py           report and exit 1 on any drift
    python tools/check_status_sync.py --list    just print what it parsed

Exit 0 = the two agree, 1 = they do not, 2 = a file was not found.
"""

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import Fail, Ok, Section, Warn, cfg  # noqa: E402

TESTTOOL = HERE.parent

# Ids that live in TEST-CASES.md but are deliberately not rows in STATUS.md.
# Each one needs a reason, because "it is special" is how a real gap hides.
NOT_A_STATUS_ROW = {
    "SD2":        "an instrument, not a case -- no PASS/FAIL criteria (sdram_diag.c)",
    "OW1-neg":    "a negative assertion inside OW1",
    "OW2-attack": "a negative assertion inside OW2",
    "DG2":        "referenced but never defined -- a hole in the matrix, tracked in COVERAGE-GAPS.md",
    "P5":         "covers the F group as a whole, not one requirement -- tracked in COVERAGE-GAPS.md",
    "H3":         "go vet is hygiene, not evidence for a requirement -- tracked in COVERAGE-GAPS.md",
    "P7":         "this check itself; it guards the table rather than the product",
    "P8":         "the one-fact-one-file check; also guards documents, not firmware",
    "S4":         "retired: SDRAM staging removed its meaning, split into S4a / S4b",
}

# Requirement ids a case may cover without STATUS.md having a row of that name.
NOT_A_REQUIREMENT = {"-", "F", "F 组"}

CASE_RE = re.compile(r"^(?:BG|T|N|S|G|OW|AU|H|K|X|DG|P|SD|M|O|EV)[0-9][0-9a-zA-Z-]*$")
REQ_RE = re.compile(r"^[A-F][0-9]{1,2}$")


def find_docs():
    """STATUS.md, and every document that defines criteria for a case.

    checklist.md is in that second set, not just TEST-CASES.md: BG1 (the boot
    gate) is defined there and nowhere else, deliberately -- it gates the three
    acceptance grids rather than being a device-behaviour case.
    """
    boot = Path(cfg.BOOT_REPO)
    status = boot / "docs" / "STATUS.md"
    cases = [TESTTOOL / "TEST-CASES.md", TESTTOOL / "acceptance" / "checklist.md"]
    missing = [str(p) for p in [status] + cases if not p.exists()]
    if missing:
        for m in missing:
            Fail("not found: %s" % m)
        return None, None
    return status, cases


def cases_from_status(path):
    """Case ids in the 用例 column, and requirement ids from the ID column.

    Rows look like:  | **B7** | ... | ✅ | **T1** | ... |
    """
    cases, reqs = {}, set()
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cols) < 4:
            continue
        rid = cols[0].strip("* ")
        if not REQ_RE.match(rid):
            continue
        reqs.add(rid)
        # The case column is the 4th; ids there may be bold, spaced, or prose
        # like "手工" / "构建门禁" / "无", which are not case ids at all.
        for tok in re.split(r"[\s,/]+", cols[3].replace("*", "")):
            tok = tok.strip("()[]。，")
            if CASE_RE.match(tok):
                cases.setdefault(tok, []).append((lineno, rid))
    return cases, reqs


def cases_from_testcases(path):
    """Case ids that TEST-CASES.md defines.

    Two shapes, both in use and both intentional:
      * a table row whose first column is the id ("| **T1** | ... |")
      * a section heading ("### P6 · the published-root warning ...")
    Missing the second shape is what made the first version of this check report
    fourteen false positives.
    """
    found = {}
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        heads = []
        if line.startswith("|"):
            heads.append(line.strip().strip("|").split("|")[0])
        m = re.match(r"^#{2,4}\s+(.*)$", line)
        if m:
            # "### S4a / S4b · ..." defines two; stop at the separator.
            heads.append(re.split(r"[·:：]", m.group(1))[0])
        # Emphasised inline, which is how the P and host groups are written --
        # they are described in the prose of their section rather than given a
        # heading of their own. The run after an arrow may be "X1/X2" or
        # "K1–K6", so take the whole run and let the splitter below deal with it.
        heads.extend(re.findall(r"\*\*([A-Za-z0-9][\w–/-]*)\*\*", line))
        heads.extend(re.findall(r"←\s*([A-Za-z0-9][\w–/-]*)", line))
        for head in heads:
            for tok in re.split(r"[\s,/]+", head.strip().strip("*` ")):
                tok = tok.strip("()[]`*、")
                if CASE_RE.match(tok):
                    found.setdefault(tok, lineno)
    return found


def expand(ids):
    """K1-K6 in one document and K1..K6 in the other are the same six cases.

    The dash may be ASCII or an en dash: TEST-CASES.md writes "K1–K6" in Chinese
    prose, selfcheck writes "K1-K6" in an id. Treating those as different ids is
    what made the first run report K1 as an orphan.
    """
    out = set()
    for i in ids:
        m = re.match(r"^([A-Z]+)(\d+)[-–](?:[A-Z]+)?(\d+)$", i)
        if m:
            pre, lo, hi = m.group(1), int(m.group(2)), int(m.group(3))
            if 0 < hi - lo < 20:
                out.update("%s%d" % (pre, n) for n in range(lo, hi + 1))
                continue
        out.add(i)
    return out


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--list", action="store_true", dest="list_only",
                    help="print what was parsed, judge nothing")
    args = ap.parse_args()

    Section("STATUS.md vs TEST-CASES.md")
    status, cases_doc = find_docs()
    if status is None:
        return 2
    print("  status  %s" % status)
    for c in cases_doc:
        print("  cases   %s" % c)

    claimed, reqs = cases_from_status(status)
    defined = {}
    for c in cases_doc:
        for cid, lineno in cases_from_testcases(c).items():
            defined.setdefault(cid, "%s:%d" % (c.name, lineno))

    if args.list_only:
        print("")
        print("  %d requirement(s), %d case id(s) claimed in STATUS.md"
              % (len(reqs), len(claimed)))
        for cid in sorted(claimed):
            print("    %-8s covers %s" % (cid, " ".join(r for _, r in claimed[cid])))
        print("")
        print("  %d case id(s) defined across the criteria docs" % len(defined))
        print("    " + " ".join(sorted(defined)))
        return 0

    claimed_x = expand(claimed)
    defined_x = expand(defined)

    problems = 0

    Section("claimed as evidence but not defined")
    orphans = sorted(claimed_x - defined_x - set(NOT_A_STATUS_ROW))
    if orphans:
        for cid in orphans:
            where = claimed.get(cid) or [(0, "?")]
            Fail("  %-8s STATUS.md:%d claims it for %s, no criteria doc defines it"
                 % (cid, where[0][0], where[0][1]))
        problems += len(orphans)
    else:
        Ok("  none")

    Section("defined but no requirement claims it")
    unclaimed = sorted(defined_x - claimed_x)
    real = [c for c in unclaimed if c not in NOT_A_STATUS_ROW]
    for cid in unclaimed:
        if cid in NOT_A_STATUS_ROW:
            print("  %-8s expected: %s" % (cid, NOT_A_STATUS_ROW[cid]))
    if real:
        for cid in real:
            Fail("  %-8s %s defines it, no STATUS.md row points at it"
                 % (cid, defined.get(cid, "?")))
        problems += len(real)
    else:
        Ok("  none unexpected")

    Section("requirement ids referenced by a case row")
    bad = sorted({r for rs in claimed.values() for _, r in rs} - reqs - NOT_A_REQUIREMENT)
    if bad:
        for r in bad:
            Fail("  %s is referenced but STATUS.md has no row defining it" % r)
        problems += len(bad)
    else:
        Ok("  all resolve")

    Section("result")
    print("  %d requirement(s), %d case(s) claimed, %d case(s) defined"
          % (len(reqs), len(claimed_x), len(defined_x)))
    if problems:
        Fail("%d mismatch(es) -- the table and the cases have drifted" % problems)
        Warn("this check cannot see a STALE status; only a structural mismatch")
        return 1
    Ok("STATUS.md and TEST-CASES.md name the same set of cases")
    Warn("it still cannot tell you whether any of those results is out of date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
