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

1. **Checkup** (read-only): measures the **always-read chain** — how many tokens every session reads before doing anything, including files your rules say to "read every session"; sorts every rule into *checkable* / *needs human judgment* / *too vague*; then tells you which rules have never been machine-checked, which serve only one subproject and should move down, and whether the root file is over its size budget. Its **reference check** looks up every path and command the rule files name — `~/…`, absolute, repo-relative, `python3 x.py`, `npm run x`, `make x` — and marks each *exists* / *broken* / *not judged*. An unsure one is never counted as passing.
2. **Teeth**: generates runnable checks for the checkable rules (Python, standard library only) into `.rule-teeth/` in your project, and wires them into Claude Code hooks with one command.
3. **Bite**: before the AI edits a file or runs a command, and again before it stops, the checks run. A broken rule blocks the action and tells the AI which rule, and on which line of which file.
4. **Audit** (read-only): replays the session logs Claude Code keeps on your machine (`~/.claude/projects/…`) against today's rules — every file edit, command and final reply — and ranks the checkable rules: **broken most often**, **never fired** (candidates to delete or demote), and **checkable but no teeth yet**; it also counts how often rule-teeth actually blocked. Which rule to fix first is decided by real violations, not by guesswork.

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

Which rules does the AI actually break? Replay your own Claude Code sessions (read-only):

```bash
python3 hands-off/rule-teeth/rule_teeth.py audit /your/project --out audit.md
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

`audit` on the same repo (18 days, 163 session files — 8,209 file edits / commands and 682 replies replayed): the most-broken rule was the reply prefix, missed in 279 replies; it got teeth only on the last day and has blocked 3 times since. 2 checkable rules never fired; 6 had no teeth yet. The reference check found 1 broken pointer and 1 command that does not say which folder to run in, out of 80 paths / commands.

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
| Forbidden tool input (MCP) | "never send `DELETE` without `WHERE` to the database tool" — **hand-written**, `init` never generates it ([below](#guarding-mcp-tools-forbid_tool_input)) | before the named tool runs |
| Must-pass command | "run `pytest` after changes" | before stopping (**off by default**; you switch it on) |
| Reply prefix | "replies start with *John —*" | before stopping |
| Always-read budget | "rules read at the start of every session total under 4,000 tokens" — counts the root, `@imports`, unscoped `.claude/rules`, and files the rules say to read every session | before edits to anything on that chain, before stopping |

## Guarding MCP tools (`forbid_tool_input`)

The hooks `init` installs match `Write|Edit|Bash`. An MCP tool (a database, GitHub, a file server) is a different tool name, so none of the checks above ever see it — the AI can do through MCP what the Bash check would have stopped. `forbid_tool_input` looks at a **named tool's input** instead. It is hand-written into `.rule-teeth/rules.json` (it needs your judgement about which tool and what is too dangerous), and **the hook's matcher in `.claude/settings.json` must list the tool**, e.g. `Write|Edit|Bash|mcp__supabase__execute_sql|mcp__supabase__apply_migration`. Claude Code reads hook config when a session starts, so a changed matcher applies to new sessions.

| key | meaning |
|---|---|
| `type` | `"forbid_tool_input"` |
| `tools` | tool names, `fnmatch` patterns, case-sensitive: `["mcp__supabase__execute_sql"]`, `["mcp__supabase__*"]` |
| `field` | which key of the tool input to scan (e.g. `"query"`). If it is not in the input — the tool renamed it — **every** string value is scanned, so a rename cannot switch the rule off |
| `sql_deny` | built-in SQL categories to block: `truncate`, `drop_table`, `drop_schema`, `drop_database`, `delete_no_where`, `update_no_where` |
| `patterns` | your own regexes. Matched against the raw string; with `"normalize": "sql"`, against each SQL statement after comments, string contents and sub-queries are stripped |
| `rule` / `source` / `enabled` / `note` | as for every other check |

A rule with no `tools`, no category and no pattern, an unknown category or a regex that does not compile is rejected when `rules.json` loads — a rule that silently blocks nothing is worse than a loud error.

```json
{"id": "RT13", "type": "forbid_tool_input", "tools": ["mcp__supabase__execute_sql"], "field": "query",
 "sql_deny": ["truncate", "drop_table", "drop_schema", "drop_database", "delete_no_where", "update_no_where"],
 "rule": "no wiping tables from the SQL tool", "source": "CLAUDE.md:12"}
{"id": "RT14", "type": "forbid_tool_input", "tools": ["mcp__supabase__apply_migration"], "field": "query",
 "sql_deny": ["truncate", "drop_schema", "drop_database", "delete_no_where", "update_no_where"],
 "rule": "migrations may drop tables, but not wipe data"}
```

**How it reads SQL.** It never connects to a database and never runs anything. It cuts the text into statements — `--` and `/* */` comments (nested ones too) become a space, `'…'` / `E'…'` / `"…"` contents are blanked, `;` splits statements, and each `( … )` group is judged as a statement of its own. So a keyword inside a string or comment does not count (`SELECT 'DROP TABLE x'` passes), a `WHERE` inside a sub-query is not the outer statement's `WHERE` (`UPDATE t SET a = (SELECT … WHERE …)` is blocked), and `WITH d AS (DELETE FROM t RETURNING *)` is not hidden. `DO $$ … $$` blocks and function bodies are scanned as code. A bare `WHERE true` / `WHERE 1=1` counts as no `WHERE`. `ON CONFLICT … DO UPDATE SET`, `FOR UPDATE`, `ON DELETE CASCADE`, `GRANT UPDATE ON …`, `CREATE TRIGGER … AFTER DELETE` are not statements and pass. The block message names the category ("DELETE without WHERE") and **never echoes the SQL** — it may hold user data.

**Tightening or loosening.** Each category is one entry in `sql_deny`. To also block `DROP TABLE` in migrations, add `"drop_table"` to RT14's list. To block something the built-ins don't cover, add a pattern: `"patterns": ["\\bDROP\\s+COLUMN\\b"], "normalize": "sql"`. To let a category through, remove it. To switch a rule off, `"enabled": false`.

**What it does not catch** (it is a guard, not a wall — it reads what the AI wrote, not what the database will do):
- SQL assembled at run time inside a string: `EXECUTE 'DELETE FROM t'`, `format(…)`, string concatenation.
- `MERGE … WHEN MATCHED THEN DELETE`; destructive statements outside the six categories (`DROP COLUMN`, `ALTER … TYPE`, `DROP FUNCTION`, `REVOKE`, …) unless you add a pattern.
- A `WHERE` that is true for every row but is not the bare `true` / `1=1` (`WHERE id = id`, `WHERE a = 1 OR true`).
- Anything that reaches the database another way (CLI, dashboard, an Edge Function, another tool name that is not in the matcher).
- False alarms exist too: a column literally named `delete` or `update`; a function *definition* that contains an unconditional `DELETE` is blocked although nothing runs yet.

## More rules without more tokens

A rule costs (how often it is loaded) × (its length), and it only works if it is in front of the AI — or a machine — when it could be broken. So each rule goes in the cheapest layer that still fires in time: **teeth** (0 tokens until broken; the block message carries the rule) → **root** (every session) → **path-scoped** (`.claude/rules` with `paths:`, subdirectory `CLAUDE.md`) → **trigger** ("read X when doing Y", skills) → **archive**. The always-read budget tooth keeps it that way as the project grows. The method, the three questions for placing a new rule, and before/after numbers: [LAYERS.md](LAYERS.md).

## Safety by design

- **Never runs commands it read in a doc on its own.** Extracted commands start switched off; a malicious `AGENTS.md` cannot make your hooks execute `curl … | sh`. You switch them on in `.rule-teeth/rules.json`.
- **The AI cannot loosen its own leash**: edits to `.rule-teeth/` and `.claude/settings*.json` are blocked.
- **Ratchet**: old problems in a file don't block unrelated edits; making things worse is blocked.
- **IOUs, not waivers**: existing violations are deferred with a due date (30 days by default) and count again once it passes.
- **Loud but not in the way**: a broken `rules.json` is reported as a visible hook error and does not block your work.
- **No infinite loops**: the stop check blocks the same set of violations at most twice, then lets the turn end with an explicit "still unresolved" message; Claude Code also caps stop-hook continuations at 8.
- **Session logs are only read.** `audit` opens them read-only, runs no commands and calls no model. Missing or empty log folders are an error, not an empty "all clear" report; lines it cannot parse are counted and shown.
- **Not judged ≠ passed.** The reference check never executes anything: it looks at the disk, the PATH, `package.json` and the Makefile. When that is not enough to know, it says so.

## How it differs from Claude Code's `/doctor prompt-audit`

prompt-audit has an AI read your instruction files and propose edits for outdated, conflicting or dangling content — it makes rules **better written**. rule-teeth turns rules into **machine-enforced checks** in hooks — it makes rules **actually followed**. Claude Code's own docs say CLAUDE.md is context, not enforced configuration, and that anything that must happen at a specific point should be a hook. rule-teeth automates that step. Use both.

## Known limits (v0.2)

- "Told to read every session" is recognised from common Chinese and English phrasings ("read X at the start of every session", a heading like "Read every session", 每个 session 都读, 开工前先读). Other phrasings are missed; phrases quoted in 「」 or "" are treated as talk about the rule, not the rule.
- Nine kinds of teeth only (the ninth, `forbid_tool_input`, is hand-written: `init` never generates it, and `audit` does not replay it). Function-length limits need per-language parsing; not done yet.
- Writing a file through Bash (`cat > a.py`) skips the pre-edit check; the stop check catches it afterwards.
- "Checked by" is a lead, not proof: a rule counts as checked when its ID or its distinctive pattern appears in a test / CI / hook file.
- When checking someone else's `AGENTS.md` alone, their repo is not scanned, so the report never claims a rule is unchecked — only that the file does not say what enforces it.
- `audit` replays **today's** rules over old sessions. A Write counts as the whole new file (the logs do not show what it replaced); an Edit counts only what it adds; line limits can only be checked on Writes; contents written through Bash are invisible. Must-pass commands and the always-read budget cannot be replayed — for those it reports only real blocks.
- The reference check describes this machine. A program missing from the PATH is *not judged* (the AI's shell may have a different PATH); `python -m x` is looked up in this machine's Python only.

## Development

```bash
python3 tests/run_all.py
```

Standard-library unittest, needs git (tests build throwaway repos). `tests/test_hook.py` drives the hook through a subprocess exactly as Claude Code does (JSON on stdin, exit code 2, stderr reason, loop guard). `tests/test_audit.py` replays the fake session logs in `tests/fixtures/sessions/`.

## Files

```
rule_teeth.py          command-line entry point
ruleteeth/
  engine.py            ★ runtime: checks + hook. One file, stdlib only, copied as-is into your project by init
  parse.py             find rule files, cut them into candidate rules
  classify.py          is it a rule? which of the three kinds? generate the check
  checkup.py           the checkup: unchecked, should-move-down, over budget, dead pointers, duplicates, partial coverage
  refs.py              reference check: do the paths and commands the rules name exist (exists / broken / not judged)
  audit.py             audit: replay local Claude Code session logs, rank rules by real violations
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
