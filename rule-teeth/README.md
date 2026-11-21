# rule-teeth

**Rules that live only in a doc are not rules.** rule-teeth reads your `CLAUDE.md` / `AGENTS.md` and turns the rules a machine can check into Claude Code hooks that bite: the moment the AI breaks one, it is stopped and told which rule, on which line.

> Part of [**hands-off**](https://github.com/JohnSUNLei/hands-off) — small, local guards that make it safe to let AI agents act for you.

[中文说明](README.zh.md)

```text
rule-teeth blocked this edit — it would break a rule written in your project's rule file:
- [RT1] app.py:2  contains `requests.post`
    rule: Never call `requests.post` directly — only through `llm.py`  (source: CLAUDE.md:3)
What to do: fix it so the rule holds, then retry. If you believe the rule truly does not apply here,
stop and tell the user; the user decides whether to change the rule.
```

Python 3.9+ and git. No `pip install`, no third-party dependencies, **no AI model is called anywhere**.

## In one line

You wrote 50 rules into `CLAUDE.md`, the AI still ignores some, so you write 80, then 100, and every session costs more tokens. rule-teeth finds the rules a machine could enforce, generates checks for them and wires them into Claude Code hooks — those rules stop depending on the AI's memory.

## What it does

1. **Checkup** (read-only): measures the **always-read chain** — how many tokens every session reads before doing anything, including files your rules say to "read every session"; sorts every rule into *checkable* / *needs human judgment* / *too vague*; then tells you which rules have never been machine-checked, which serve only one subproject and should move down, whether the root file is over its size budget, and which paths it points to no longer exist.
2. **Teeth**: generates runnable checks for the checkable rules (Python, standard library only) into `.rule-teeth/` in your project, and wires them into Claude Code hooks with one command.
3. **Bite**: before the AI edits a file or runs a command, and again before it stops, the checks run. A broken rule blocks the action and tells the AI which rule, and on which line of which file.

Classification is word lists and regular expressions; checks are regexes, counts and commands. The same rule file always gets the same verdict.

## 30-second start

```bash
git clone https://github.com/JohnSUNLei/hands-off
```

Check up your project (read-only, changes nothing):

```bash
python3 hands-off/rule-teeth/rule_teeth.py checkup /your/project --out checkup.md
```

Give the checkable rules teeth and wire them into Claude Code:

```bash
python3 hands-off/rule-teeth/rule_teeth.py init /your/project --hook
```

Changed your mind? One command removes the hooks:

```bash
python3 hands-off/rule-teeth/rule_teeth.py unhook /your/project
```

The engine is copied into your project's `.rule-teeth/`, so the clone is only needed for `checkup` / `init` / `unhook`.

Don't like typing commands? Tell Claude Code: "Run a rule-teeth checkup on this project and show me the report."

## Sample output

On the author's own private monorepo (16 subprojects, built by a founder who does not write code):

> **In one line**: 51 rules, **42 have never been checked by a machine** (82%) — they rely on the AI remembering; 4 can get teeth right now.
>
> **Top finding**: every session reads ~14,945 tokens of rules before doing anything; the root file is only 3,251 of them — 4 files come in because the rules say to read them every session.

After one afternoon of moving rules into the right layers: 3,737 tokens per session, no rule deleted. How: [LAYERS.md](LAYERS.md).

On public projects (read-only; locations and categories only, no rule text reproduced): [vercel/next.js AGENTS.md](demo/public/vercel-next.js_AGENTS.md_checkup.md) is 33KB / 550 lines — nearly 3× Claude Code's recommended 200 lines, ~9,400 tokens in every session — and 8 of its 71 rules can become hooks today, e.g. "do not add *Generated with Claude Code* footers to commits" and "do not run `gh pr ready`". Also: [openai/openai-agents-python](demo/public/openai-agents-python_AGENTS.md_checkup.md).

## Three kinds of rules

| Category | Meaning | Examples |
|---|---|---|
| **Checkable** | The rule names something a machine recognizes: a pattern / command / file in backticks, an explicit number of lines, a secret, a reply prefix | "files under 1500 lines", "never use `requests.post`, only via `llm.py`", "Do NOT mark PRs as ready (`gh pr ready`)" |
| **Needs judgment** | Clear, but you need to understand it to judge it. The report says why, and shows how to split out a checkable part when there is one | "every major decision gets a devil's-advocate pass" |
| **Too vague** | Not even a person could say whether it was met. The report suggests a rewrite | "code should be elegant", "keep commit messages concise" |

When unsure, rule-teeth **files a rule under "needs judgment" rather than generate a check that blocks the wrong things**. A check that cries wolf is soon ignored.

## Kinds of teeth

| Tooth | Comes from rules like | Bites |
|---|---|---|
| Line / size limit | "files under N lines", "root file ≤ 10KB / 200 lines" | before edits, before stopping |
| Forbidden pattern | "never use `X`", "`X` only in `Y`" | before edits, before stopping |
| No secrets | "never commit API keys" (8 built-in key formats) | before edits, before stopping |
| Protected path | "do not edit `docs/ja`" | before edits |
| Forbidden command | "do NOT `gh pr ready`", "use `pnpm`, not `npm`", "no *Generated with…* in commits" | before commands |
| Must-pass command | "run `pytest` after changes" | before stopping (**off by default**; you switch it on) |
| Reply prefix | "replies start with *John —*" | before stopping |
| Always-read budget | "rules read at the start of every session total under 4,000 tokens" — counts the root, `@imports`, unscoped `.claude/rules`, and files the rules say to read every session | before edits to anything on that chain, before stopping |

## More rules without more tokens

A rule costs (how often it is loaded) × (its length), and it only works if it is in front of the AI — or a machine — when it could be broken. So each rule goes in the cheapest layer that still fires in time: **teeth** (0 tokens until broken; the block message carries the rule) → **root** (every session) → **path-scoped** (`.claude/rules` with `paths:`, subdirectory `CLAUDE.md`) → **trigger** ("read X when doing Y", skills) → **archive**. The always-read budget tooth keeps it that way as the project grows. The method, the three questions for placing a new rule, and before/after numbers: [LAYERS.md](LAYERS.md).

## Safety by design

- **Never runs commands it read in a doc on its own.** Extracted commands start switched off; a malicious `AGENTS.md` cannot make your hooks execute `curl … | sh`. You switch them on in `.rule-teeth/rules.json`.
- **The AI cannot loosen its own leash**: edits to `.rule-teeth/` and `.claude/settings*.json` are blocked.
- **Ratchet**: old problems in a file don't block unrelated edits; making things worse is blocked.
- **IOUs, not waivers**: existing violations are deferred with a due date (30 days by default) and count again once it passes.
- **Loud but not in the way**: a broken `rules.json` is reported as a visible hook error and does not block your work.
- **No infinite loops**: the stop check blocks the same set of violations at most twice, then lets the turn end with an explicit "still unresolved" message; Claude Code also caps stop-hook continuations at 8.

## How it differs from Claude Code's `/doctor prompt-audit`

prompt-audit has an AI read your instruction files and propose edits for outdated, conflicting or dangling content — it makes rules **better written**. rule-teeth turns rules into **machine-enforced checks** in hooks — it makes rules **actually followed**. Claude Code's own docs say CLAUDE.md is context, not enforced configuration, and that anything that must happen at a specific point should be a hook. rule-teeth automates that step. Use both.

## Known limits (v0.2)

- "Told to read every session" is recognised from common Chinese and English phrasings ("read X at the start of every session", a heading like "Read every session", 每个 session 都读, 开工前先读). Other phrasings are missed; phrases quoted in 「」 or "" are treated as talk about the rule, not the rule.
- Eight kinds of teeth only. Function-length limits need per-language parsing; not done yet.
- Writing a file through Bash (`cat > a.py`) skips the pre-edit check; the stop check catches it afterwards.
- "Checked by" is a lead, not proof: a rule counts as checked when its ID or its distinctive pattern appears in a test / CI / hook file.
- When checking someone else's `AGENTS.md` alone, their repo is not scanned, so the report never claims a rule is unchecked — only that the file does not say what enforces it.

## Development

```bash
python3 tests/run_all.py
```

Standard-library unittest, needs git (tests build throwaway repos). `tests/test_hook.py` drives the hook through a subprocess exactly as Claude Code does (JSON on stdin, exit code 2, stderr reason, loop guard).

## Files

```
rule_teeth.py          command-line entry point
ruleteeth/
  engine.py            ★ runtime: checks + hook. One file, stdlib only, copied as-is into your project by init
  parse.py             find rule files, cut them into candidate rules
  classify.py          is it a rule? which of the three kinds? generate the check
  checkup.py           the checkup: unchecked, should-move-down, over budget, dead pointers, duplicates, partial coverage
  report.py            Markdown report (English / Chinese)
  install.py           write .rule-teeth/, add / remove the hook
  model.py             data types (each concept defined once)
tests/                 the test suite
LAYERS.md              the layering method: five layers, three questions, the always-read budget
demo/                  checkups of two public AGENTS.md files
```

## Part of hands-off

Small, deterministic, local tools for the accidents that happen once you let AI agents act for you. One file each, standard library only, no model calls.

| tool | stops |
|---|---|
| **rule-teeth** | the rules in `CLAUDE.md` / `AGENTS.md` that the agent quietly ignores |
| [send-fuse](../send-fuse) | an email or message that should never have gone out |
| [tripwire](../tripwire) | a web page talking your agent into leaking your keys |
| [blackbox](../blackbox) | "what did it do all night?" — a plain-language timeline, and undo |
| [brake](../brake) | a stuck agent burning money all night |
| [local-first](../local-first) | paying the cloud for work a model on your machine could do |

⭐ [Star hands-off](https://github.com/JohnSUNLei/hands-off) to follow the series. Questions, bugs, or want guardrails like these around your team's agents? [Open an issue](https://github.com/JohnSUNLei/hands-off/issues).

[MIT License](LICENSE).
