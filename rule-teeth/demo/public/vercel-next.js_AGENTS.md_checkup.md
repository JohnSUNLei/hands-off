# Rule checkup · vercel/next.js — AGENTS.md (canary branch, fetched 2026-11-21)

> rule-teeth 0.1.0 · 2026-11-21 · read-only checkup, no files were changed

> (public project: locations and categories only, rule text not reproduced)

**In one line**: 71 rules; 8 can become Claude Code hook checks right now, the rest need human judgment or are too vague. (Only this one file was read; its repo was not scanned.)

## Overview

| | |
|---|---|
| Rule files | 1 (1 auto-loaded into every session) |
| Rules | 71 (plus 154 passages that are explanations / indexes / examples, not rules) |
| Checkable automatically | 8 |
| Needs human judgment | 62 |
| Too vague | 1 |
| Already machine-checked | 0 |
| Never checked | **71** (100%) |

- `AGENTS.md` auto

## Top 4 findings

1. **`AGENTS.md` is over the limit: 33,017 bytes / 550 lines (limit 10KB / 200 lines); every session pays ~9,414 tokens for it**
2. **8 rules can get teeth right now (checks generated: forbid_command, forbid_pattern, no_secrets); one command wires them into Claude Code hooks**
   - AGENTS.md:198 — → forbid `check(`, only in *test*, *spec*, test/
   - AGENTS.md:404 — → no secrets in code / config (8 built-in key formats)
   - AGENTS.md:405 — → no secrets in code / config (8 built-in key formats)
   - … +5
3. **Reading the rule file alone: none of the 71 rules says which check enforces it (their repo was not scanned; checks may exist elsewhere)**
   - AGENTS.md:73 — → It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook.
   - AGENTS.md:75 — → Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically.
   - AGENTS.md:103 — → Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically.
   - … +68
4. **1 rule is too vague for anyone, human or machine, to tell whether it was followed**
   - AGENTS.md:451 — → "concise" cannot be checked; even a human cannot say whether it was met. Say it concretely: which file, which pattern, how many lines, which command.

## Rule files auto-loaded into every session

| File | Bytes | Lines | ≈tokens | Status |
|---|---:|---:|---:|---|
| `AGENTS.md` | 33,017 | 550 | 9,414 | ❌ over (322%) |

## Checkable rules — teeth you can install with one command

| Where | Rule | Generated check | Status |
|---|---|---|---|
| AGENTS.md:198 | — | forbid `check(`, only in *test*, *spec*, test/ | checkable, not installed yet |
| AGENTS.md:404 | — | no secrets in code / config (8 built-in key formats) | checkable, not installed yet |
| AGENTS.md:405 | — | no secrets in code / config (8 built-in key formats) | checkable, not installed yet |
| AGENTS.md:407 | — | no secrets in code / config (8 built-in key formats) | checkable, not installed yet |
| AGENTS.md:450 | — | block commands `commit/PR … Generated with Claude Code` | checkable, not installed yet |
| AGENTS.md:453 | — | block commands `gh pr ready` | checkable, not installed yet |
| AGENTS.md:507 | — | forbid `browser.back(`, only in *test*, *spec*, test/ | checkable, not installed yet |
| AGENTS.md:513 | — | forbid `super::` | checkable, not installed yet |

Install: `python3 rule_teeth.py init . --hook`. Command checks (run tests at Stop) start switched off; you turn them on by hand in `.rule-teeth/rules.json` — the tool never decides on its own to run commands for you.

## Vague rules, and how to rewrite them

| Where | Rule | How to fix |
|---|---|---|
| AGENTS.md:451 | — | "concise" cannot be checked; even a human cannot say whether it was met. Say it concretely: which file, which pattern, how many lines, which command. |

## All rules

| # | Where | Rule | Category | Status |
|---|---|---|---|---|
| 1 | AGENTS.md:73 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 2 | AGENTS.md:75 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 3 | AGENTS.md:103 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 4 | AGENTS.md:105 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 5 | AGENTS.md:142 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 6 | AGENTS.md:147 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 7 | AGENTS.md:167 | — (soft) | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 8 | AGENTS.md:172 | — | judgment | It is about how the AI talks or communicates; files cannot show it. |
| 9 | AGENTS.md:177 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 10 | AGENTS.md:184 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 11 | AGENTS.md:198 | — | checkable | checkable, not installed yet |
| 12 | AGENTS.md:211 | — (soft) | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 13 | AGENTS.md:227 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 14 | AGENTS.md:248 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 15 | AGENTS.md:250 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 16 | AGENTS.md:257 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 17 | AGENTS.md:272 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 18 | AGENTS.md:277 | — | judgment | It is about how the AI talks or communicates; files cannot show it. |
| 19 | AGENTS.md:282 | — | judgment | It is about how the AI talks or communicates; files cannot show it. |
| 20 | AGENTS.md:284 | — (soft) | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 21 | AGENTS.md:285 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 22 | AGENTS.md:286 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 23 | AGENTS.md:287 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 24 | AGENTS.md:288 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 25 | AGENTS.md:289 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 26 | AGENTS.md:291 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 27 | AGENTS.md:295 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 28 | AGENTS.md:322 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 29 | AGENTS.md:329 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 30 | AGENTS.md:333 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 31 | AGENTS.md:335 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 32 | AGENTS.md:337 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 33 | AGENTS.md:339 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 34 | AGENTS.md:341 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 35 | AGENTS.md:345 | — | judgment | It is about how the AI talks or communicates; files cannot show it. |
| 36 | AGENTS.md:354 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 37 | AGENTS.md:356 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 38 | AGENTS.md:371 | — | judgment | It is about how the AI talks or communicates; files cannot show it. |
| 39 | AGENTS.md:390 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 40 | AGENTS.md:391 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 41 | AGENTS.md:397 | — (soft) | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 42 | AGENTS.md:398 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 43 | AGENTS.md:402 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 44 | AGENTS.md:404 | — | checkable | checkable, not installed yet |
| 45 | AGENTS.md:405 | — | checkable | checkable, not installed yet |
| 46 | AGENTS.md:406 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 47 | AGENTS.md:407 | — | checkable | checkable, not installed yet |
| 48 | AGENTS.md:422 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 49 | AGENTS.md:450 | — | checkable | checkable, not installed yet |
| 50 | AGENTS.md:451 | — (soft) | vague | too vague |
| 51 | AGENTS.md:453 | — | checkable | checkable, not installed yet |
| 52 | AGENTS.md:455 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 53 | AGENTS.md:467 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 54 | AGENTS.md:468 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 55 | AGENTS.md:485 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 56 | AGENTS.md:486 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 57 | AGENTS.md:488 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 58 | AGENTS.md:492 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 59 | AGENTS.md:493 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 60 | AGENTS.md:494 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 61 | AGENTS.md:495 | — | judgment | It says where something must live, but not what is forbidden elsewhere. Put that code pattern in backticks and it becomes checkable. |
| 62 | AGENTS.md:500 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 63 | AGENTS.md:502 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 64 | AGENTS.md:503 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 65 | AGENTS.md:504 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 66 | AGENTS.md:505 | — | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 67 | AGENTS.md:507 | — | checkable | checkable, not installed yet |
| 68 | AGENTS.md:513 | — | checkable | checkable, not installed yet |
| 69 | AGENTS.md:531 | — (soft) | judgment | Needs understanding to judge. If part of it can name a file, a code pattern or a number, that part can be checked automatically. |
| 70 | AGENTS.md:536 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |
| 71 | AGENTS.md:548 | — | judgment | It is about order of work: what the AI did, not what files look like. If it boils down to "must have run command X before finishing", it can become a Stop hook. |

## How rules were classified

- **Checkable**: the rule names something a machine can recognise — a code pattern / command / file in backticks, a line count, secrets, how replies start — and rule-teeth generated a check for it.
- **Needs judgment**: clear, but you have to understand it to judge it ("every major decision needs a counter-argument"). The table says why, and how to split off a checkable part when there is one.
- **Too vague**: nobody can tell whether it was followed ("write clean code").
- Classification is word lists and regular expressions, no model calls: the same file always gets the same verdict. When unsure it says "needs judgment" rather than generate a check that blocks the wrong things.
- "Checked by" means a test / CI / hook file contains the rule's ID or a pattern unique to it. A lead, not proof — worth a look.

