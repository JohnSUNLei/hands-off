# demo · sample checkups

| File | What it is |
|---|---|
| `public/vercel-next.js_AGENTS.md_checkup.md` | The `AGENTS.md` of vercel/next.js (canary branch, fetched 2026-11-21), read-only |
| `public/openai-agents-python_AGENTS.md_checkup.md` | The `AGENTS.md` of openai/openai-agents-python (main branch, fetched 2026-11-21), read-only |

Reports on other people's projects are generated with `--no-quote`: line numbers, categories and the generated checks only — no rule text is reproduced. No PRs were opened and nobody was contacted.

Regenerate:

```bash
python3 rule_teeth.py checkup --file downloaded/AGENTS.md --as AGENTS.md --name "owner/repo — AGENTS.md" --lang en --no-quote --out demo/public/xxx.md
```
