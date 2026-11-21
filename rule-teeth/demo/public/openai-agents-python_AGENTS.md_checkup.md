# Rule checkup · openai/openai-agents-python — AGENTS.md (main branch, fetched 2026-11-21)

> rule-teeth 0.1.0 · 2026-11-21 · read-only checkup, no files were changed

> (public project: locations and categories only, rule text not reproduced)

**In one line**: 42 rules; 1 can become Claude Code hook checks right now, the rest need human judgment or are too vague. (Only this one file was read; its repo was not scanned.)

## Overview

| | |
|---|---|
| Rule files | 1 (1 auto-loaded into every session) |
| Rules | 42 (plus 26 passages that are explanations / indexes / examples, not rules) |
| Checkable automatically | 1 |
| Needs human judgment | 41 |
| Too vague | 0 |
| Already machine-checked | 0 |
| Never checked | **42** (100%) |

- `AGENTS.md` auto

## Top 3 findings

1. **`AGENTS.md` is over the limit: 16,627 bytes / 106 lines (limit 10KB / 200 lines); every session pays ~4,750 tokens for it**
2. **1 rule can get teeth right now (checks generated: protected_path); one command wires them into Claude Code hooks**
   - AGENTS.md:77 — → block edits to docs/ja, docs/ko, docs/zh
3. **Reading the rule file alone: none of the 42 rules says which check enforces it (their repo was not scanned; checks may exist elsewhere)**
   - AGENTS.md:5 — → A human decision; a machine should not judge it.
   - AGENTS.md:6 — → A human decision; a machine should not judge it.
   - AGENTS.md:7 — → It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook.
   - … +39

## Rule files auto-loaded into every session

| File | Bytes | Lines | ≈tokens | Status |
|---|---:|---:|---:|---|
| `AGENTS.md` | 16,627 | 106 | 4,750 | ❌ over (162%) |

## Checkable rules — teeth you can install with one command

| Where | Rule | Generated check | Status |
|---|---|---|---|
| AGENTS.md:77 | — | block edits to docs/ja, docs/ko, docs/zh | checkable, not installed yet |

Install: `python3 rule_teeth.py init . --hook`. Command checks (run tests at Stop) start switched off; you turn them on by hand in `.rule-teeth/rules.json` — the tool never decides on its own to run commands for you.

## All rules

| # | Where | Rule | Category | Status |
|---|---|---|---|---|
| 1 | AGENTS.md:5 | — | judgment | A human decision; a machine should not judge it. |
| 2 | AGENTS.md:6 | — | judgment | A human decision; a machine should not judge it. |
| 3 | AGENTS.md:7 | — (soft) | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 4 | AGENTS.md:8 | — | judgment | A human decision; a machine should not judge it. |
| 5 | AGENTS.md:10 | — | judgment | A human decision; a machine should not judge it. |
| 6 | AGENTS.md:16 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 7 | AGENTS.md:17 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 8 | AGENTS.md:18 | — | judgment | A human decision; a machine should not judge it. |
| 9 | AGENTS.md:19 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 10 | AGENTS.md:20 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 11 | AGENTS.md:21 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 12 | AGENTS.md:22 | — | judgment | A human decision; a machine should not judge it. |
| 13 | AGENTS.md:26 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 14 | AGENTS.md:27 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 15 | AGENTS.md:28 | — (soft) | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 16 | AGENTS.md:29 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 17 | AGENTS.md:30 | — (soft) | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 18 | AGENTS.md:36 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 19 | AGENTS.md:37 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 20 | AGENTS.md:38 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 21 | AGENTS.md:40 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 22 | AGENTS.md:41 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 23 | AGENTS.md:51 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 24 | AGENTS.md:53 | — | judgment | A human decision; a machine should not judge it. |
| 25 | AGENTS.md:54 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 26 | AGENTS.md:55 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 27 | AGENTS.md:61 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 28 | AGENTS.md:63 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 29 | AGENTS.md:75 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 30 | AGENTS.md:77 | — | checkable | checkable, not installed yet |
| 31 | AGENTS.md:79 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 32 | AGENTS.md:80 | — (soft) | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 33 | AGENTS.md:86 | — (soft) | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 34 | AGENTS.md:87 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 35 | AGENTS.md:92 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 36 | AGENTS.md:93 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 37 | AGENTS.md:94 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 38 | AGENTS.md:96 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 39 | AGENTS.md:102 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 40 | AGENTS.md:103 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 41 | AGENTS.md:104 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 42 | AGENTS.md:105 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |

## How rules were classified

- **Checkable**: the rule names something a machine can recognise — a code pattern / command / file in backticks, a line count, secrets, how replies start — and rule-teeth generated a check for it.
- **Needs judgment**: clear, but you have to understand it to judge it ("every major decision needs a counter-argument"). The table says why, and how to split off a checkable part when there is one.
- **Too vague**: nobody can tell whether it was followed ("write clean code").
- Classification is word lists and regular expressions, no model calls: the same file always gets the same verdict. When unsure it says "needs judgment" rather than generate a check that blocks the wrong things.
- "Checked by" means a test / CI / hook file contains the rule's ID or a pattern unique to it. A lead, not proof — worth a look.

