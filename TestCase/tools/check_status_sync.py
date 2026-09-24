"""A requirement and the case it cites have to agree, both ways.

Case P7. Reads the module documents under $PROD/docs/modules/, where both
tables live: the feature table names requirements and the case each one leans
on, the test table names cases and the requirement each one covers.

What it catches:

  * a case cited as evidence that no test table defines
    (a requirement pointing at a case nobody can run);
  * a case defined in a test table that no requirement cites
    (a test whose result nobody records, so nobody notices when it rots);
  * a requirement id in a test row's back-reference column that no feature
    table defines.

The third one is new. Before the restructure the case tables had no column
pointing back at the requirement, so a test could claim to cover anything.

What it does NOT catch: a status that is simply out of date -- "T3-01 failed
yesterday but R3-01 still says ✅". That needs the run result to reach the
table by itself, and today a person types it in.

    python tools/check_status_sync.py           report and exit 1 on any drift
    python tools/check_status_sync.py --list    just print what it parsed

Exit 0 = they agree, 1 = they do not, 2 = a document was not found.
"""

import argparse
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import Fail, Ok, Section, Warn, cfg, prod_docs  # noqa: E402

TESTTOOL = HERE.parent

# Ids that live in TEST-CASES.md but are deliberately not rows in STATUS.md.
# Each one needs a reason, because "it is special" is how a real gap hides.
NOT_A_STATUS_ROW = {
    "T2-02":      "a negative assertion inside T2-01",
    "T2-04":      "a negative assertion inside T2-03",
    "P5":         "covers the F group as a whole, not one requirement",
    "H3":         "go vet is hygiene, not evidence for a requirement",
    "P7":         "this check itself; it guards the table rather than the product",
    "P8":         "the one-fact-one-file check; also guards documents, not firmware",
    "P9":         "the documented-path check; also guards documents, not firmware",
    "P10":        "the allow-list hygiene check; advisory, guards local config, not firmware",
    "P12":        "wayfinder ticket hygiene; guards the issue tracker, not firmware",
    "P13":        "the stale-id check; also guards documents, not firmware",
    "P14":        "the CHANGE-LIST orphan gate; guards the planning artefacts, not firmware",
    "P17":        "the .cproject linker script guard; protects the port-tool build, not firmware",
    "S4":         "retired: SDRAM staging removed its meaning, split into T1-21 / T1-22",
    }

# The acceptance grids are the steps of a procedure, not evidence for one
# requirement: ENG-07 requires that the checklist exist and catch the bundled
# upgrade risk, and these rows are what is inside it. CHK-C* is different --
# ENG-09 cites that grid directly, so it is not listed here.
NOT_A_STATUS_ROW.update(
    {"CHK-A%d" % i: "a step of the post-change self-check grid" for i in range(1, 8)})
NOT_A_STATUS_ROW.update(
    {"CHK-B%d" % i: "a step of the release grid" for i in range(1, 9)})

# Requirement ids a case may cover without STATUS.md having a row of that name.
NOT_A_REQUIREMENT = {"-", "F", "F 组"}

# T1-07 / T1-18a / CHK-C3 / BG1 / P6 -- the new scheme plus the ids that
# deliberately kept their own (static checks, the acceptance grids).
CASE_RE = re.compile(r"^(?:T[1-4]-[0-9]{2}[a-z]?|CHK-[A-C][0-9]+|(?:BG|OW|AU|H|K|X|P|SD|M|O|EV|S|G|N|T)[0-9][0-9a-zA-Z-]*)$")
REQ_RE = re.compile(r"^(?:R[1-4]-[0-9]{2}|ENG-[0-9]{2})$")


# Evidence and back-reference cells are prose: "手工 + `T1-09`", "P2（查跨仓
# 镜像没分叉，不是这条功能本身）", "`T1-18a`–`T1-18g`". Splitting on spaces
# alone leaves the full-width bracket glued to the id, and leaves a range as
# one unreadable token.
SPLIT_RE = re.compile(r"[\s,/+（）()\[\]、。，]+")
RANGE_RE = re.compile(r"^([A-Z]\d-\d\d)([a-z])?[-–]([A-Z]\d-\d\d)([a-z])?$")
# CHK-C1–CHK-C7: same idea, a different id shape.
CHK_RANGE_RE = re.compile(r"^(CHK-[A-C])(\d+)[-–](?:CHK-[A-C])?(\d+)$")


CRITERIA_DOC_NAME = "$PROD/docs/engineering/HOW-TO-RUN-TESTS.md"


def catalog_ids():
    """The step ids selfcheck actually runs, read from its CATALOG.

    Parsed rather than imported: selfcheck.py probes the toolchain at import
    time, and this check must work on a machine that has none of it.
    """
    src = (HERE / "selfcheck.py").read_text(encoding="utf-8", errors="replace")
    body = src.split("CATALOG = [", 1)[1].split("]", 1)[0]
    raw = {m.group(1) for m in re.finditer(r'\(\s*"([^"]+)"', body)} - {"ENV"}
    out = set()
    for label in raw:
        out.update(expand_catalog_label(label))
    return out


def expand_catalog_label(label):
    """One CATALOG row may stand for a run of cases: T1-18a-T1-18g, T1-19-T1-20.

    Deliberately narrow -- only the two shapes selfcheck actually writes. A
    label that is just one id comes back unchanged, and an unrecognised range
    stays whole so it surfaces as undocumented rather than silently vanishing.
    """
    m = re.match(r"^(T\d-\d\d)([a-z])-(T\d-\d\d)([a-z])$", label)
    if m and m.group(1) == m.group(3):
        return ["%s%s" % (m.group(1), chr(c))
                for c in range(ord(m.group(2)), ord(m.group(4)) + 1)]
    m = re.match(r"^(T\d)-(\d\d)-(T\d)-(\d\d)$", label)
    if m and m.group(1) == m.group(3):
        lo, hi = int(m.group(2)), int(m.group(4))
        if 0 < hi - lo < 20:
            return ["%s-%02d" % (m.group(1), n) for n in range(lo, hi + 1)]
    return [label]


def ids_in(cell):
    """Every case id a table cell names, ranges expanded."""
    out = []
    # Backticks come off before splitting: a range is written with each end
    # quoted separately (`T1-18a`–`T1-18g`), so leaving them in hides the dash.
    for tok in SPLIT_RE.split(cell.replace("*", "").replace("`", "")):
        tok = tok.strip("`。，、 ")
        if not tok:
            continue
        m = CHK_RANGE_RE.match(tok)
        if m:
            pre, a, b = m.group(1), int(m.group(2)), int(m.group(3))
            if 0 <= b - a < 40:
                out.extend("%s%d" % (pre, k) for k in range(a, b + 1))
            continue
        m = RANGE_RE.match(tok)
        if m:
            lo, lo_s, hi, hi_s = m.groups()
            if lo_s and hi_s and lo == hi:          # T1-18a–T1-18g
                out.extend("%s%s" % (lo, chr(c))
                           for c in range(ord(lo_s), ord(hi_s) + 1))
            elif not lo_s and not hi_s:             # T2-01–T2-06
                pre, a = lo.rsplit("-", 1)
                b = hi.rsplit("-", 1)[1]
                if pre == hi.rsplit("-", 1)[0] and 0 <= int(b) - int(a) < 40:
                    out.extend("%s-%02d" % (pre, n)
                               for n in range(int(a), int(b) + 1))
            continue
        if CASE_RE.match(tok):
            out.append(tok)
    return out


def find_docs():
    """The four module documents, and every document that defines a case.

    ACCEPTANCE-CHECKLIST.md is in the second set alongside them: BG1 (the boot
    gate) and the CHK grids are defined there and nowhere else, deliberately --
    they gate the acceptance runs rather than being device-behaviour cases.
    """
    prod = prod_docs()
    if prod is None:
        Fail("no OpenPLC_Docs clone found, so the module documents cannot be located.")
        Warn("  clone it, then: python tools/init_machine.py --redetect DOCS_REPO")
        return None, None
    mods = sorted((prod / "docs" / "modules").glob("M[0-9]-*.md"))
    eng = prod / "docs" / "engineering" / "README.md"
    if eng.is_file():
        mods.append(eng)
    checklist = prod / "docs" / "tables" / "ACCEPTANCE-CHECKLIST.md"
    # The static checks P1-P12 are defined by their sections here, not in a
    # module test table -- they guard the repository rather than the device.
    howto = prod / "docs" / "engineering" / "HOW-TO-RUN-TESTS.md"
    # Fails rather than skipping when they are absent: a skip would pass
    # vacuously on exactly the machine that is set up wrong.
    if not mods:
        Fail("no module document found under %s" % (prod / "docs" / "modules"))
        return None, None
    if not checklist.exists():
        Fail("not found: %s" % checklist)
        return None, None
    extra = [checklist] + ([howto] if howto.is_file() else [])
    return mods, mods + extra


def cases_from_status(paths):
    """Requirement ids, and the case ids each one leans on for evidence.

    Feature rows look like:
      | **R1-02** | 入口通道 | ...通过以太网... | 手工 + `T1-09` | ✅ |

    The evidence column is prose as often as not -- "手工", "代码审查",
    "静态检查 P2（查跨仓镜像没分叉）". Only the tokens shaped like a case id
    count; the rest is a statement that no case covers this, which is a fact
    about the requirement, not a drift.
    """
    cases, reqs = {}, set()
    for path in paths:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.startswith("|"):
                continue
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            # Module feature rows are | # | 阶段 | 要做到什么 | 谁证明 | 状态 |
            # and the engineering table drops 阶段, so the evidence column is
            # the second-to-last either way.
            if len(cols) < 4:
                continue
            rid = cols[0].strip("*` ")
            if not REQ_RE.match(rid):
                continue
            reqs.add(rid)
            for tok in ids_in(cols[-2]):
                cases.setdefault(tok, []).append((lineno, rid))
    return cases, reqs


def cases_from_testcases(path, table_only):
    """Case ids a document defines, and the requirement each one claims.

    table_only is on for the module documents: their test tables are the
    definition, and harvesting ids out of prose as well reads "G1 认领"
    (generation 1 of the owner chain) as the staging case T1-14. The acceptance
    checklist has no such table, so there it stays off.
    """
    found, covers = {}, {}
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        heads = []
        if line.startswith("|"):
            cols = [c.strip() for c in line.strip().strip("|").split("|")]
            head = cols[0].strip("*` ")
            if CASE_RE.match(head):
                heads.append(head)
                # Column 2 is the back-reference, present only in a test table.
                if len(cols) > 1:
                    for tok in SPLIT_RE.split(cols[1].replace("*", "")):
                        tok = tok.strip("`。，、 ")
                        if REQ_RE.match(tok):
                            covers.setdefault(head, set()).add(tok)
        if not table_only:
            m = re.match(r"^#{2,4}\s+(.*)$", line)
            if m:
                heads.append(re.split(r"[·:：]", m.group(1))[0])
            heads.extend(re.findall(r"\*\*([A-Za-z0-9][\w–/-]*)\*\*", line))
        for head in heads:
            for tok in re.split(r"[\s,/]+", head.strip().strip("*` ")):
                tok = tok.strip("()[]`*、")
                if CASE_RE.match(tok):
                    found.setdefault(tok, lineno)
    return found, covers


def expand(ids):
    """K1-K7 in one document and K1..K7 in the other are the same seven cases.

    The dash may be ASCII or an en dash: TEST-CASES.md writes "K1–K7" in Chinese
    prose, selfcheck writes "K1-K7" in an id. Treating those as different ids is
    what made the first run report K1 as an orphan.
    """
    out = set()
    for i in ids:
        # T1-07 is one case id, not the range T1..T7. Only the old single-letter
        # scheme ever wrote ranges, and it never used a leading zero.
        if re.match(r"^[A-Z]\d-\d\d", i):
            out.add(i)
            continue
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

    Section("requirements vs the cases they cite")
    mods, cases_doc = find_docs()
    if mods is None:
        return 2
    for m in mods:
        print("  module  %s" % m)
    for c in cases_doc:
        if c not in mods:
            print("  cases   %s" % c)

    claimed, reqs = cases_from_status(mods)
    defined, covers = {}, {}
    for c in cases_doc:
        found, cov = cases_from_testcases(c, table_only=(c in mods))
        # The how-to is the home of the static checks and nothing else: the
        # device cases it still names are pre-rename text, and the module test
        # tables are the registry now.
        if c.name == "HOW-TO-RUN-TESTS.md":
            found = {k: v for k, v in found.items() if k.startswith("P")}
        for cid, lineno in found.items():
            defined.setdefault(cid, "%s:%d" % (c.name, lineno))
        for cid, rs in cov.items():
            covers.setdefault(cid, set()).update(rs)

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
            Fail("  %-8s line %d cites it for %s, no test table defines it"
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
            Fail("  %-8s %s defines it, no requirement cites it"
                 % (cid, defined.get(cid, "?")))
        problems += len(real)
    else:
        Ok("  none unexpected")

    Section("requirement ids a case claims to cover")
    bad = sorted({r for rs in covers.values() for r in rs} - reqs - NOT_A_REQUIREMENT)
    if bad:
        for r in bad:
            Fail("  %s is claimed by a case but no feature table defines it" % r)
        problems += len(bad)
    else:
        Ok("  all resolve")

    Section("every step selfcheck runs is documented")
    # The third registration place. P7 used to read only the module tables, so a
    # step could sit in selfcheck's CATALOG, run on every invocation, and appear
    # in no document at all -- which is exactly how P14 stayed invisible from
    # 2026-09-20 to 2026-09-21.
    undocumented = sorted(catalog_ids() - defined_x - set(NOT_A_STATUS_ROW))
    if undocumented:
        for cid in undocumented:
            Fail("  %-8s is in selfcheck's CATALOG but no document defines it" % cid)
            Fail("     add a row to %s, or give it a reason in NOT_A_STATUS_ROW"
                 % CRITERIA_DOC_NAME)
        problems += len(undocumented)
    else:
        Ok("  every CATALOG step has a documented criterion")

    Section("result")
    print("  %d requirement(s), %d case(s) claimed, %d case(s) defined"
          % (len(reqs), len(claimed_x), len(defined_x)))
    if problems:
        Fail("%d mismatch(es) -- the requirements and the cases have drifted" % problems)
        Warn("this check cannot see a STALE status; only a structural mismatch")
        return 1
    Ok("every cited case is defined, and every defined case is cited")
    Warn("it still cannot tell you whether any of those results is out of date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
