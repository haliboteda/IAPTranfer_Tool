"""Compares the Arduino core the IDE actually loads (CORE_LIVE) against the git
repo (CORE_REPO).

The direction is one-way by design: edit and verify in CORE_LIVE, then copy the
verified result into CORE_REPO and commit. CORE_LIVE is not under version
control, so anything verified there and not copied across exists on exactly one
machine and dies with the next IDE reinstall.

This is release-checklist item B3, automated.

Exit 0 = identical, 1 = differences, 2 = a repo path is wrong.

M7 step 2: a translation of check-core-sync.ps1.
"""

import hashlib
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import cfg, Section, Ok, Warn, Fail                     # noqa: E402

for p in (cfg.CORE_LIVE, cfg.CORE_REPO):
    if not Path(p).is_dir():
        Fail("not a directory: %s" % p)
        sys.exit(2)

# Six deliberate exclusions:
#   installed.json          the IDE's own install metadata, not source
#   tools/discovery/bin/    Go build output; the sources next to it are enough
#   *~                      editor backups
#   .claude/                agent-local permissions, gitignored in CORE_REPO --
#                           so it can never be "verified in live but uncommitted",
#                           yet it drifts on every session and reported DIFF
#   .vscode/                same shape: an editor writes it into whichever folder
#                           you open. 2026-08-21 the CMake extension put a
#                           settings.json holding one absolute local path into
#                           the live package, and A9 went red on it. It is
#                           gitignored in CORE_REPO too, so it can never be
#                           "verified in live but uncommitted" either
#   .gitignore              exists only on the repo side, by definition
#   CLAUDE.md               repo-side entry doc; must not ship inside the board
#                           package the IDE installs
#
# A check that is red every single run is one nobody reads.
#
# re.I because PowerShell's -match is case-insensitive; without it CLAUDE.md
# would still be skipped but Claude.md would not, and the check would go red on
# a rename nobody made.
#
# This pattern must stay identical to the one in check-core-sync.ps1: M7 step 2
# holds the two versions to byte-identical output, so fixing one alone breaks
# the comparison rather than the check.
SKIP = re.compile(
    r'^(installed\.json|\.gitignore$|CLAUDE\.md$|\.claude[\\/]|\.vscode[\\/]|tools[\\/]discovery[\\/]bin[\\/])|~$',
    re.I)


def walk_files(root):
    """Files under root, deterministic order, .git pruned.

    Get-ChildItem -Recurse without -Force omits hidden items, and git marks
    .git hidden on Windows -- so pruning it here is what keeps the two versions
    looking at the same file set, not an optimisation.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted((d for d in dirnames if d != ".git"), key=str.lower)
        for name in sorted(filenames, key=str.lower):
            yield os.path.join(dirpath, name)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


only_live = []
diff = []
only_repo = []

live_root = str(Path(cfg.CORE_LIVE).resolve()).rstrip("\\/")
repo_root = str(Path(cfg.CORE_REPO).resolve()).rstrip("\\/")

Section("core: live vs repo")
print("  live  %s" % live_root)
print("  repo  %s" % repo_root)

live_rel = {}
for full in walk_files(live_root):
    rel = full[len(live_root) + 1:]
    if SKIP.search(rel):
        continue
    live_rel[rel] = True
    r = os.path.join(repo_root, rel)
    if not os.path.exists(r):
        only_live.append(rel)
        continue
    if sha256(full) != sha256(r):
        diff.append(rel)

# The reverse direction matters too: a file deleted in live but still committed
# means the repo would reinstate dead code on the next package build.
for full in walk_files(repo_root):
    rel = full[len(repo_root) + 1:]
    if SKIP.search(rel):
        continue
    if re.match(r'^\.git[\\/]', rel, re.I):
        continue
    if rel not in live_rel:
        only_repo.append(rel)

for f in only_live:
    Fail("ONLY-LIVE  %s" % f)
for f in diff:
    Fail("DIFF       %s" % f)
for f in only_repo:
    Warn("ONLY-REPO  %s" % f)

Section("result")
n = len(only_live) + len(diff)
if n > 0:
    Fail("%d file(s) verified in live but not in the repo -- copy them across and commit" % n)
    if only_repo:
        Warn("plus %d file(s) present only in the repo" % len(only_repo))
    sys.exit(1)
if only_repo:
    Warn("live matches the repo, but %d file(s) exist only in the repo" % len(only_repo))
    sys.exit(0)
Ok("live and repo are identical")
sys.exit(0)
