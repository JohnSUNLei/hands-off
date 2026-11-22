# -*- coding: utf-8 -*-
"""命令行入口。

    rule_teeth.py checkup [项目目录]          体检报告（只读）
    rule_teeth.py checkup --file AGENTS.md   只体检一份规则文件（别人项目的也行）
    rule_teeth.py init [项目目录] [--hook]    生成检查；--hook 同时挂进 Claude Code
    rule_teeth.py unhook [项目目录]           把 hook 摘掉
    rule_teeth.py check [项目目录]            按已生成的检查扫一遍
    rule_teeth.py audit [项目目录]            实测榜：拿本机会话记录重放，哪条规则最常被违反（只读）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import audit, checkup, engine, install, report

HELP = {
    "checkup": "体检：哪些规则从没被检查过、该下沉、根文件超没超 / checkup report (read-only)",
    "init": "把能查的规则变成检查，写进 .rule-teeth/ / turn checkable rules into checks",
    "unhook": "从 .claude/settings.json 摘掉 rule-teeth 的 hook / remove rule-teeth hooks",
    "check": "按 .rule-teeth/rules.json 扫一遍项目 / scan the project with the generated checks",
    "audit": "实测榜：重放本机 Claude Code 会话记录，哪条规则最常被违反、从没触发、还没装牙（只读）"
             " / replay local session logs: most-broken, never-fired and toothless rules (read-only)",
}


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="rule_teeth.py",
                                 description="rule-teeth：只写在文档里的规则等于没有规则 / rules that bite")
    ap.add_argument("-V", "--version", action="version", version=engine.VERSION)
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("checkup", help=HELP["checkup"])
    c.add_argument("root", nargs="?", default=".")
    c.add_argument("--file", help="只体检这一份规则文件 / check just this one rule file")
    c.add_argument("--name", default="", help="报告标题里的项目名 / project name for the title")
    c.add_argument("--as", dest="shown_as", default="", help="--file 时报告里显示的文件名 / file name to show")
    c.add_argument("--out", help="写到文件；不给就打印 / write report to this file")
    c.add_argument("--lang", choices=("zh", "en"), default="", help="报告语言，默认跟规则文件 / report language")
    c.add_argument("--include", action="append", default=[], help="额外当规则文件读 / extra rule file")
    c.add_argument("--no-quote", action="store_true", help="不转载规则原文（体检别人的项目用）/ do not reproduce rule text")
    c.add_argument("--max-lines", type=int, default=checkup.DEFAULT_MAX_LINES)
    c.add_argument("--max-kb", type=int, default=checkup.DEFAULT_MAX_KB)
    c.add_argument("--top", type=int, default=5)

    i = sub.add_parser("init", help=HELP["init"])
    i.add_argument("root", nargs="?", default=".")
    i.add_argument("--hook", action="store_true", help="同时写进 .claude/settings.json / also install Claude Code hooks")
    i.add_argument("--local", action="store_true", help="写 settings.local.json（只对你自己生效）/ personal settings")
    i.add_argument("--dry-run", action="store_true", help="只打印，不写文件 / print only")
    i.add_argument("--include", action="append", default=[])
    i.add_argument("--defer-days", type=int, default=30, help="现有违规登记暂缓多少天 / IOU length for existing violations")

    u = sub.add_parser("unhook", help=HELP["unhook"])
    u.add_argument("root", nargs="?", default=".")
    u.add_argument("--local", action="store_true")

    k = sub.add_parser("check", help=HELP["check"])
    k.add_argument("root", nargs="?", default=".")
    k.add_argument("--run", action="store_true", help="也跑规则要求的命令 / also run required commands")

    a = sub.add_parser("audit", help=HELP["audit"])
    a.add_argument("root", nargs="?", default=".")
    a.add_argument("--sessions", action="append", default=[],
                   help="会话记录目录，可给多个；默认 ~/.claude/projects/<项目路径编码>/ 和它的 worktree / session log folder(s)")
    a.add_argument("--since", default="", help="只看这天以后的 YYYY-MM-DD / only sessions since this date")
    a.add_argument("--out", help="写到文件；不给就打印 / write report to this file")
    a.add_argument("--lang", choices=("zh", "en"), default="")
    a.add_argument("--name", default="", help="报告标题里的项目名 / project name for the title")
    a.add_argument("--top", type=int, default=10)
    a.add_argument("--include", action="append", default=[], help="额外当规则文件读 / extra rule file")
    return ap


def _checkup(a) -> int:
    if a.file:
        cu = checkup.run_file(a.file, a.lang, a.shown_as, a.max_lines, a.max_kb)
    else:
        cu = checkup.run(a.root, a.include, a.lang, a.max_lines, a.max_kb)
    text = report.render(cu, name=a.name, quote=not a.no_quote, top=a.top)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"→ {a.out}")
        print(text.split("\n\n")[2] if text.count("\n\n") > 2 else "")
    else:
        print(text)
    return 0


def _init(a) -> int:
    s = install.init(a.root, a.hook, a.local, a.dry_run, a.include, a.defer_days)
    zh = s["lang"] == "zh"
    on = [c for c in s["checks"] if c.get("enabled", True)]
    print(("生成检查 {n} 条（开着 {on} 条），现有违规登记暂缓 {b} 处" if zh else
           "{n} checks generated ({on} on); {b} existing violations deferred").format(
        n=len(s["checks"]), on=len(on), b=sum(x["count"] for x in s["baseline"])))
    for c in s["checks"]:
        flag = "on " if c.get("enabled", True) else "off"
        print(f"  [{c['id']}] {flag} {report.describe_check(c, s['lang'])}   ← {c['source']}")
    if s["dry_run"]:
        print("（--dry-run：没写任何文件）" if zh else "(--dry-run: nothing written)")
    for w in s["written"]:
        print(f"→ {w}")
    if not a.hook and not a.dry_run:
        print("还没挂 hook。要让 Claude Code 当场拦：加 --hook 再跑一次。" if zh else
              "Hooks not installed yet. To make Claude Code block violations on the spot, run again with --hook.")
    return 0


def _audit(a) -> int:
    try:
        au = audit.run(a.root, a.sessions, a.since, a.lang, a.include)
    except audit.AuditError as e:
        print(f"rule-teeth audit: {e}", file=sys.stderr)
        return 2
    if au.bad_lines:
        print(f"rule-teeth audit: {au.bad_lines} 行不是合法 JSON，跳过了 / lines skipped (not valid JSON)", file=sys.stderr)
    for u in au.unreadable:
        print(f"rule-teeth audit: 读不了 / unreadable: {u}", file=sys.stderr)
    text = audit.render(au, name=a.name, top=a.top)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"→ {a.out}")
        print(audit.summary(au))
    else:
        print(text)
    return 0


def main(argv=None) -> int:
    a = _parser().parse_args(argv)
    if a.cmd == "checkup":
        return _checkup(a)
    if a.cmd == "init":
        return _init(a)
    if a.cmd == "unhook":
        p = install.unhook(a.root, a.local)
        print(f"→ {p}" if p else "no settings file; nothing to remove")
        return 0
    if a.cmd == "check":
        return engine.main(["check", a.root] + (["--run"] if a.run else []))
    if a.cmd == "audit":
        return _audit(a)
    return 2


if __name__ == "__main__":
    sys.exit(main())
