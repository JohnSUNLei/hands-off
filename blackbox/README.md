# blackbox

**Your AI worked all night. What did it actually do? Open one page to find out — and undo any step with one command.**

blackbox is a flight recorder for coding agents: every command, every file it changed (even through shell commands), every web page it read, in a plain-language timeline — with the before and after of every file, so one bad step can be put back without losing the rest of the night's work.

> Part of [**hands-off**](https://github.com/JohnSUNLei/hands-off) — small, local guards that make it safe to let AI agents act for you.

![blackbox demo: the morning after, the timeline shows the step that zeroed all prices; one command undoes it](promo/blackbox_demo.gif)

```bash
curl -fsSLo .blackbox/blackbox.py --create-dirs https://raw.githubusercontent.com/JohnSUNLei/hands-off/main/blackbox/blackbox.py && python3 .blackbox/blackbox.py init
```

One Python file. Standard library only. No model calls, no account, no network, no daemon.
Works with Claude Code today (via hooks). [中文说明](README.zh.md)

---

## The morning after

You left the agent with "tidy up the checkout code and get the tests passing". In the morning it says:

> Done. Tests pass (4/4). I normalized the price data and removed old backups.

"Normalized the price data" turns out to mean every price is now `0`, and the backups are gone. Which command did that? When? What did the file look like before? Was anything else touched?

```bash
python3 .blackbox/blackbox.py report --open
```

![the timeline page](promo/blackbox_timeline.png)

```
$ python3 .blackbox/blackbox.py undo 6
Undo step 6 — Normalize price fields — `sed -i '' 's/"price": [0-9.]*/"price": 0/' data/prices.json`:
  restore  data/prices.json
Done. Step 6 is undone (recorded as step 12; `undo 12` brings it back).
```

The good edit to `checkout.py` from step 4 and the new `NOTES.md` from step 10 stay exactly as they are.

## What it records

| | |
|---|---|
| what you asked | every prompt (`UserPromptSubmit`) — the timeline is grouped by them |
| what it ran | every shell command, with the agent's own one-line description and the tail of the output; failures marked |
| what it changed | every file created, edited or deleted, **including files changed by shell commands** (`sed -i`, `rm -rf`, scripts, generators), with line counts and the full diff |
| what it read | web pages (`WebFetch`), searches, files it looked at (hidden by default), MCP tools it used, helper agents it started |
| what changed around it | files changed *between* steps by you, your editor or another program — so they are never blamed on the agent, and never silently overwritten by an undo |
| what it said | the agent's last message each time it stopped |

Steps worth a look are flagged: deletions, failed commands, `rm -rf`, `git push` / `reset --hard` / `clean -f`, network commands, writes outside the project.

## How it works

- `init` copies the engine to `.blackbox/`, adds six hooks to `.claude/settings.json` (your other settings are untouched), and snapshots the project once as a starting point.
- Before each file edit or shell command, blackbox notes the state of the project; after it, it diffs. Contents are stored once per version (content-addressed, zlib-compressed), so a long night costs megabytes, not gigabytes. `node_modules`, `.git`, `.venv`, build folders and files over 2 MB are skipped (configurable).
- It records through Claude Code's documented hook interface — not by parsing the session transcript, whose format is internal and changes between versions.
- `undo N` puts every file that step N touched back the way it was before step N: edited files restored, created files removed, deleted files recreated. If a *later* step changed the same file, it refuses and tells you which step (use `--force` to overwrite anyway). The undo is recorded as a new step, so `undo` of the undo brings the change back.
- The agent may read the record and run `list` / `show` / `report` / `undo`, but it cannot edit or delete `.blackbox/`: a black box the pilot can erase is not a black box.

## Commands

```
python3 .blackbox/blackbox.py report [--open]   # write (and open) .blackbox/timeline.html
python3 .blackbox/blackbox.py list [N]          # last N steps in the terminal
python3 .blackbox/blackbox.py show N            # one step: its output and full diff
python3 .blackbox/blackbox.py undo N [--force]  # put back every file step N changed
python3 .blackbox/blackbox.py uninstall         # remove our hooks and .blackbox/ (--keep-data keeps the record)
```

Options for `init`: `--lang zh` (Chinese timeline and messages), `--local` (write hooks to `.claude/settings.local.json`).
Config: `.blackbox/config.json` — `max_file_mb`, `skip_dirs`, `skip_files`, `output_chars`, `lang`.
`skip_files` (e.g. `[".env", ".env.*", "*.pem"]`) keeps secrets out of the record: a change to such a file is noted, its contents are never stored, and `undo` leaves it alone.

The record (`.blackbox/steps.jsonl`, `objects/`) stays on your machine and out of git; only the engine and config are meant to be committed.

## How is this different from…

- **git** — only sees what you commit, and doesn't know which agent step made which change, why, or what the agent read on the way.
- **Claude Code's built-in checkpoints (`/rewind`)** — rewind code and/or conversation to an earlier *prompt*, for files Claude edited with its edit tools; per its docs, a file changed only by shell commands isn't tracked. blackbox sees shell changes too, undoes one step while keeping everything after it, and gives you a page you can read (or send to someone) the next morning.
- **session viewers** — show the transcript; blackbox shows the *effects* (files before / after) and can reverse them.

## Limits

- Changes outside the project folder (e.g. `rm ~/somefile`) are flagged but not snapshotted, so they can't be undone.
- Two shell commands running *in parallel* can't be told apart: their changes may be attributed to both.
- It undoes files, not the world: a `git push`, a sent email, a database write or a deployment stays done. The timeline at least tells you it happened.
- Files over `max_file_mb` (default 2 MB) are noted but not kept.
- Works with Claude Code today. Other agents need a small adapter that feeds the same events (PRs welcome).

Verified end to end with Claude Code 2.1.322: a real session's `Write`, a `sed -i` run through Bash and an `echo` were recorded as three plain-language steps, and `undo` restored the file the `sed` had changed.

## Tests

```bash
python3 tests/run_all.py
```

26 tests; fixtures are the exact JSON Claude Code sends to hooks (`tests/fixtures/`), replayed against a real temp project.

## Part of hands-off

Small, deterministic, local tools for the accidents that happen once you let AI agents act for you. One file each, standard library only, no model calls.

| tool | stops |
|---|---|
| [rule-teeth](../rule-teeth) | the rules in `CLAUDE.md` / `AGENTS.md` that the agent quietly ignores |
| [send-fuse](../send-fuse) | an email or message that should never have gone out |
| [tripwire](../tripwire) | a web page talking your agent into leaking your keys |
| **blackbox** | "what did it do all night?" — a plain-language timeline, and undo |
| [brake](../brake) | a stuck agent burning money all night |
| [local-first](../local-first) | paying the cloud for work a model on your machine could do |

⭐ [Star hands-off](https://github.com/JohnSUNLei/hands-off) to follow the series. Questions, bugs, or want guardrails like these around your team's agents? [Open an issue](https://github.com/JohnSUNLei/hands-off/issues).

[MIT License](LICENSE).
