# Layered rules: more rules, and every session still reads only what it needs

[中文](LAYERS.zh.md) · part of [rule-teeth](README.md)

**In one line**: a rule costs (how often it is loaded) × (how long it is). It works only if it is in front of the AI — or a machine — at the moment it could be broken.
So put every rule in **the cheapest layer that still fires at the right moment**. Then adding rules does not raise the fixed cost of every session, and having many rules does not mean they get ignored.

## Measure first: the real fixed cost is not your root file

Everyone counts the lines in `CLAUDE.md`. But what the AI reads before doing anything is a whole chain:

> **The always-read chain** = the root `CLAUDE.md` + its `@imports` (expanded at launch, up to four hops) + `.claude/rules/*.md` files without `paths:` + **every file X the rules say to "read at the start of every session"**.

That last part is a **soft import**. It is not on Claude Code's load list, the official "keep it under 200 lines" advice never sees it, and `/context` only shows it after the fact — but the AI does as it is told, so it is a real fixed cost.

Measured by rule-teeth on the author's own repos (≈tokens):

| Project | Root file | Always-read chain | What is most expensive on the chain |
|---|---:|---:|---|
| Main repo (16 subprojects) | 3,251 | **14,945** | The root says "every session, read the methodology"; the methodology says "then read the dispatch handbook and the business context" |
| Education project | 3,499 | **13,683** | The same methodology chain |
| Video project | 1,066 | **16,220** | The root lists 5 files to "read before starting" |
| Document-heavy folder | 3,725 | **42,804** | One 36,000-token "read this first" file |

Every root file is under 200 lines and looks compliant. The real cost is 4–15× the root file. Results after the cleanup are at the end.

## Five layers: each rule goes in the cheapest layer that is still in time

| Layer | When it enters the context | Cost per session | What makes it hold | What goes here | How, in Claude Code |
|---|---|---|---|---|---|
| **0 Teeth** | Only at the moment it is broken (the block message carries the rule text and its source line) | 0 | A machine: a hook blocks on the spot | Anything a machine can check: line counts, forbidden code, secrets, protected files, forbidden commands, how replies start, the always-read budget | `rule_teeth.py init --hook`; tests, CI |
| **1 Root** | Every session | Highest | The AI's memory | Rules every subproject needs, that any task might hit, and that a machine cannot check; the subproject index | Root `CLAUDE.md`; a small shared part via `@import` |
| **2 Path** | Loaded automatically when matching files are touched | Only tasks that touch them pay | Timing: it appears right when that code is being changed | Rules for one subproject or one kind of file | A subdirectory `CLAUDE.md`; `.claude/rules/*.md` with `paths:` |
| **3 Trigger** | Read when doing a certain kind of task | Only that kind of task pays | The index says "read X when doing Y"; skill descriptions | Procedures, checklists, long handbooks, personas | Skills (only the description stays loaded); docs plus a trigger |
| **4 Archive** | Never loaded automatically | 0 | — | Changelogs, decision history, retros | Separate files that no "read every session" points to |

Layer 0 is the key: **the block message is itself an on-demand rule** — it loads only at the moment of the violation, only the rule that was broken, with its source. Once a rule has teeth, the root needs at most a one-line pointer ("no hard-coded keys (enforced)"), or nothing.

Layer 2 is underrated. It does not just save tokens; it puts the rule **closest to the moment it can be broken**. Rule #80 in a root file is read at launch and is long buried by the time the AI edits that file. A rule with `paths:` shows up when the AI opens that file.

## Where does a new rule go? Three questions

1. **Can a machine check it?** Yes → layer 0. Give it teeth; keep at most a one-line pointer at the root.
   If not, try rewriting it to **leave evidence**: "every major decision needs a counter-argument" cannot be checked; "every decision record has a 'Counter-argument' section" can. Teeth check evidence, not intentions.
2. **Who needs it?** Every subproject → root. One or some → layer 2. **Important is not the same as universal**: however important, if only one subproject uses it, it moves down.
3. **When is it needed?** Every task → root or layer 2. One kind of task → layer 3, with the trigger written down. Never in daily work → layer 4.

When unsure, put it one layer lower. Moving a rule up later is adding a line; moving it down means finding it first, which is much harder.

## As the project grows: keeping it this way without relying on memory

A rule that only lives in a document is not a rule — and that includes the layering rule. So it gets teeth too:

1. **The always-read budget** (`load_budget` tooth). Write one sentence in the root file, for example:
   `Rules read at the start of every session (this file + @imports + .claude/rules without paths + files the rules say to read every session) total under 4,000 tokens.`
   `rule_teeth.py init --hook` turns it into a hook. Before the AI edits a rule file — or a file that is soft-imported — the whole chain is recomputed; **if it grows past the budget, the edit is blocked**, and the message lists the biggest files on the chain and how to cut. A chain that is already over may only shrink (a ratchet).
2. **Root file size** (`max_lines` tooth): "root file under 200 lines / 10KB".
3. **Rules about rules go in layer 2**: the layering method itself lives in `.claude/rules/` with `paths: ["**/CLAUDE.md"]`. It is in front of the AI exactly when someone is editing rules, and costs nothing the rest of the time.
4. **A regular checkup** (`rule_teeth.py checkup`): rules no machine has ever checked, rules that should move down, dead pointers, rules written twice, rules whose checks cover only some subprojects.
5. **IOUs, not exemptions**: when teeth are installed, existing violations are registered as IOUs that expire in 30 days.
6. **The AI cannot loosen its own leash**: the hook blocks AI edits to `.rule-teeth/` and `.claude/settings*.json`. Whether to raise the budget is a human decision.

What budget? If none is written, the checkup uses 4,000 tokens as a reference line (about the official 200 lines, in Chinese). English projects can go lower.

## Retrofitting an existing project in 30 minutes

1. `python3 rule_teeth.py checkup /your/project` — look at the first finding, the always-read chain.
2. **Cut soft imports first**: turn "read X every session" into "read X when doing Y". If a few lines of X really are needed every time, split them into a small file. (Pointing an `@import` or a `.claude/rules/` symlink outside the project needs a one-time "external imports" approval, and if it is declined the file silently does not load — so check that it loads.)
3. **Rules in the root that serve one subproject** → that subproject's own `CLAUDE.md`, or `.claude/rules/x.md` with `paths:`. Move them without rewording and leave a one-line pointer.
4. **Rules about rules** (the layering method, size limits) → `.claude/rules/` with `paths: ["**/CLAUDE.md"]`.
5. Write one budget sentence; `init --hook`.
6. Run `checkup` again and compare.

## Common mistakes

- **"Just write the rules shorter."** Compressing wording saves little and loses meaning. Move rules; do not squeeze them.
- **"Split the root file with `@import`."** It saves nothing: imports are expanded at launch. Their use is sharing one source of truth.
- **"Mark it 'must read' and the AI will read it."** It will — that is why it costs. And the more it reads up front, the less of it sticks.
- **"Important rules belong in the root."** Important is not universal. A rule with teeth is far safer than a rule in the root.

## After the cleanup (same day, same repos)

| Project | Before | After | Saved |
|---|---:|---:|---:|
| Main repo (16 subprojects) | 14,945 | **3,737** | −75% |
| Education project | 13,683 | **4,111** | −70% |
| Video project | 16,220 | **2,465** | −85% |
| The methodology folder itself | 7,299 | **4,154** | −43% |

What changed (main repo):

- The methodology chain went from 4 files (11,694 tokens) to one 1,196-token "core" file: the ten rules every task needs. Personas, the dispatch handbook and the business context became "read when doing Y".
- The layering rules themselves moved into `.claude/rules/` with `paths:` pointing at rule files: loaded only while rules are being edited.
- A section that only governs Python moved into `.claude/rules/` with `paths: ["**/*.py"]`.
- Seven "other docs" cells in the subproject index repeated exactly what each subproject's entry file already lists; they were removed.
- One budget sentence, with teeth: anyone who adds to the chain past the budget is stopped on the spot.

**No rule was deleted.** Every one is still there, in a different layer: the checkable ones grew teeth, the ones serving one subproject moved down, the on-demand ones got their triggers written down.
The document-heavy folder was left alone: that big file is the single current source of truth for something in progress, and whether to split it is the owner's call. The tool's job is to put the number on the table.
