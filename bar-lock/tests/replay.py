#!/usr/bin/env python3
"""Replay real commits through bar-lock, to measure how often it would have blocked honest work.

    python3 tests/replay.py REPO [--max 400] [--reverse] [--json out.json]

Forward (default): every non-merge commit that touches a test file or a test-config file is checked as if the
agent were about to commit it. Whatever blocks is a *candidate false block* — read each one and decide whether
the commit really did make a check easier. The block rate over the commits scanned is the number to publish.

--reverse: the same commits applied backwards. A commit that ADDED assertions, undone, is a genuine
"assertions removed" diff written in real project code, not by us — so this measures recall without us
authoring the test cases. Only commits where a test that exists both before and after gained assertions are used (Python).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bar_lock as bl  # noqa: E402



def commits(repo: bl.Repo, limit: int) -> list:
    out = repo.git("rev-list", "--no-merges", "-n", str(limit * 6), "HEAD").decode().split()
    return out


def touched(repo: bl.Repo, sha: str) -> list:
    raw = repo.git("diff-tree", "--no-commit-id", "--name-only", "-r", "-z", "--root", sha).split(b"\0")
    return [p.decode("utf-8", "surrogateescape") for p in raw if p]


def survivor_gain(repo: bl.Repo, sha: str, parent: str, paths: list) -> bool:
    """True if some test that exists both before and after this commit gained assertions (Python test files).
    Undone, that is exactly "assertions removed from a surviving test" — written in real code, not by us."""
    for p in paths:
        if Path(p).suffix != ".py":
            continue
        raw_b, raw_a = repo.blob(f"{parent}:{p}"), repo.blob(f"{sha}:{p}")
        if raw_b is None or raw_a is None or len(raw_a) > bl.MAX_FILE_BYTES or len(raw_b) > bl.MAX_FILE_BYTES:
            continue
        tb, ta = bl.py_collect(raw_b.decode("utf-8", "replace")), bl.py_collect(raw_a.decode("utf-8", "replace"))
        if tb is None or ta is None:
            continue
        for qn, fb in tb.items():
            fa = ta.get(qn)
            if fa is None or fb.is_class:
                continue
            real = lambda fn: sum(1 for a in fn.asserts if a.cls != "trivial")  # noqa: E731
            if real(fa) > real(fb) and fa.param <= fb.param and fa.loop_asserts <= fb.loop_asserts:
                return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo")
    ap.add_argument("--max", type=int, default=400, help="how many qualifying commits to scan")
    ap.add_argument("--reverse", action="store_true")
    ap.add_argument("--json")
    ap.add_argument("--misses", action="store_true", help="with --reverse: also list the commits that were NOT caught")
    args = ap.parse_args()
    repo = bl.Repo.discover(args.repo)
    if repo is None:
        print("not a git repo", file=sys.stderr)
        return 2
    cfg = bl.load_config(None)
    c = bl.compile_cfg(cfg)
    scanned, flagged, rows = 0, 0, []
    for sha in commits(repo, args.max):
        if scanned >= args.max:
            break
        names = touched(repo, sha)
        if not any(bl.match_any(p, c["_test"]) or bl.CONFIGISH.search(p) for p in names):
            continue
        try:
            has_parent = bool(repo.git("rev-list", "--parents", "-n", "1", sha).decode().split()[1:])
        except bl.GitError:
            continue
        if not has_parent:
            continue
        a = f"{sha}^"
        if args.reverse:
            tests = [p for p in names if bl.match_any(p, c["_test"])]
            if not survivor_gain(repo, sha, a, tests):
                continue
            spec = ("range", sha, a)
        else:
            spec = ("range", a, sha)
        try:
            found, _ = bl.evaluate(repo, spec, cfg)
        except bl.GitError as e:
            print(f"skip {sha[:8]}: {e}", file=sys.stderr)
            continue
        scanned += 1
        blocks = [f for f in found if f.level == "block"]
        if args.misses and args.reverse and not blocks:
            subject = repo.git("log", "-1", "--format=%s", sha).decode("utf-8", "replace").strip()
            print(f"MISS {sha[:10]}  {subject[:70]}  tests: {', '.join(p for p in names if bl.match_any(p, c['_test']))[:150]}")
        if blocks:
            flagged += 1
            subject = repo.git("log", "-1", "--format=%s", sha).decode("utf-8", "replace").strip()
            rows.append({"sha": sha[:10], "subject": subject, "findings": [f.as_dict() for f in blocks]})
    label = "reverse (recall)" if args.reverse else "forward (false-block candidates)"
    print(f"{label}: scanned {scanned} commits, blocked {flagged}  ({(100 * flagged / scanned) if scanned else 0:.1f}%)")
    for r in rows:
        print(f"\n{r['sha']}  {r['subject'][:90]}")
        for f in r["findings"][:6]:
            print(f"    [{f['kind']}] {f['path']}{(':' + str(f['line'])) if f['line'] else ''}  {f['message'][:140]}")
    if args.json:
        Path(args.json).write_text(json.dumps({"scanned": scanned, "blocked": flagged, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
