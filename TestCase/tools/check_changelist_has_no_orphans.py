"""No unfinished work may live only in a map's CHANGE-LIST.

A CHANGE-LIST is an implementation inventory: it says what an effort will have
to touch, including everything already done. It is NOT a todo list, and nothing
scans it -- not the frontier script (which reads tickets), not work/TODO.md
(whose admission rule wants a closed ticket per row), not selfcheck. So an item
that is still outstanding can sit there indefinitely with nothing to surface it.

That is exactly what happened: `flashboot`, the compressed 'R' record format,
`setowner --wipe` and the release-notes rewrite were all designed, written down,
and then invisible to every tool. They were found by reading back a chat log.

This check makes the arrangement self-enforcing. Every CHANGE-LIST must carry a
banner naming where its unfinished work went, and each block it names must be
findable in work/TODO.md. Add a block to one side and forget the other, and this
goes red.

What it does NOT check: whether an individual table row is done. Those rows mark
who decides, not whether the code exists -- conflating the two is what made the
list unable to answer "how much is left" in the first place. The banner is the
contract; the rows stay a reference.

Exit code is the verdict: 0 every CHANGE-LIST is accounted for, 1 one is not.
"""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from common import Fail, Ok, Section, Warn, cfg  # noqa: E402

DOCS = Path(cfg.DOCS_REPO)
MAPS = DOCS / "maps"
TODO = DOCS / "work" / "TODO.md"

# The banner a CHANGE-LIST must carry, and the line that names one block.
BANNER = "还没做的那几块，已经搬去"
BLOCK = re.compile(r"[-—]{2}\s*(.+?)。", re.S)


def blocks_from_banner(text):
    """The block names a CHANGE-LIST says it handed to TODO, or None if no banner."""
    i = text.find(BANNER)
    if i < 0:
        return None
    # The banner paragraph ends at the next blank line after the em-dash list.
    tail = text[i:i + 2000]
    m = BLOCK.search(tail)
    if not m:
        return []
    return [b.strip().strip("`") for b in re.split(r"[、,，]", m.group(1)) if b.strip()]


def main():
    Section("CHANGE-LIST orphans")
    if not TODO.exists():
        Fail("no work/TODO.md at %s" % TODO)
        return 1
    todo = TODO.read_text(encoding="utf-8", errors="replace")

    lists = sorted(MAPS.glob("*/CHANGE-LIST.md"))
    if not lists:
        Ok("no CHANGE-LIST in any map; nothing to account for")
        return 0

    problems = 0
    for cl in lists:
        name = cl.parent.name
        text = cl.read_text(encoding="utf-8", errors="replace")
        blocks = blocks_from_banner(text)
        if blocks is None:
            Fail("  %s: no banner saying where its unfinished work went" % name)
            Fail("     add a line starting %r naming the blocks, or say there are none"
                 % BANNER)
            problems += 1
            continue
        if not blocks:
            Warn("  %s: banner found but names no blocks" % name)
            continue
        for b in blocks:
            # A block counts as tracked when TODO mentions a distinctive part of
            # its name. Keep the comparison loose: TODO phrases things for a
            # reader, not as an identifier.
            key = max(re.findall(r"[A-Za-z_'`-]{4,}|[一-鿿]{3,}", b) or [b], key=len)
            if key.strip("`") in todo:
                Ok("  %-34s %s" % (name, b))
            else:
                Fail("  %-34s %s -- not found in work/TODO.md" % (name, b))
                Fail("     either put it in TODO, or drop it from the banner")
                problems += 1

    print("")
    if problems:
        Fail("%d block(s) of unfinished work live only in a CHANGE-LIST" % problems)
        return 1
    Ok("every CHANGE-LIST says where its unfinished work went, and TODO has it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
