# rule-teeth · 规则长牙

**只写在文档里的规则等于没有规则。** rule-teeth 读你的 `CLAUDE.md` / `AGENTS.md`，把能让机器检查的规则变成 Claude Code 里会咬人的 hook：AI 一违反，当场拦下，并告诉它违反的是哪条、写在第几行。

> 属于 [**hands-off（敢放手）**](https://github.com/JohnSUNLei/hands-off) 系列 —— 让 AI 替你干活也不出事的几个本地小工具。

[English](README.md)

```text
rule-teeth blocked this edit — it would break a rule written in your project's rule file:
- [RT1] app.py:2  contains `requests.post`
    rule: Never call `requests.post` directly — only through `llm.py`  (source: CLAUDE.md:3)
What to do: fix it so the rule holds, then retry. If you believe the rule truly does not apply here,
stop and tell the user; the user decides whether to change the rule.
```

要求 Python 3.9 以上、git。不用 `pip install`，没有第三方依赖，**全程不调任何 AI 模型**。

## 一句话

你在 `CLAUDE.md` 里写了 50 条规则，AI 还是不听，于是你写到 80 条、100 条，token 越来越贵。rule-teeth 帮你找出哪几条其实能让机器来管，生成检查、挂进 Claude Code 的 hook —— 从此那几条不用再靠 AI 记性。

## 它做四件事

1. **体检**（只读）：先算**每次必读链** —— 每个 session 开工前固定要读多少 token，连规则里写着「每个 session 都读」的文件一起算；再把每条规则分成三类 —— 能自动检查 / 要人判断 / 写得太模糊；再告诉你哪些从来没被机器检查过、哪些只服务一个子项目该下沉、根文件是不是超了体量。其中**规则验真**把规则文件点名的路径和命令（`~/…`、绝对路径、仓库里的相对路径、`python3 x.py`、`npm run x`、`make x`）挨个查一遍，标成「在 / 失效 / 未能判断」—— 拿不准的不算通过。
2. **装牙**：能查的规则生成可运行的检查（Python，只用标准库），写进项目里的 `.rule-teeth/`，一条命令挂进 Claude Code 的 hook。
3. **咬人**：AI 改文件、跑命令之前先过一遍；要结束之前再过一遍。违反了就拦下，并告诉 AI 违反的是哪条、写在第几行。
4. **实测榜**（只读）：拿 Claude Code 存在你电脑上的会话记录（`~/.claude/projects/…`），把历史上每次改文件、跑命令、每轮最后一句回复，对着现在的规则重放一遍，排出三张榜：**最常被违反的**、**从没触发的**（候选删掉或降级）、**能查却还没装牙的**；另外数 rule-teeth 真拦过几次。先改哪条，按真实违规次数排，不再凭感觉。

分类靠词表和正则，检查靠正则、计数和跑命令。同一份规则永远得到同一个结论。

## 30 秒上手

```bash
git clone https://github.com/JohnSUNLei/hands-off
```

体检你的项目（只读，不改任何文件）：

```bash
python3 hands-off/rule-teeth/rule_teeth.py checkup /你的项目 --out 体检报告.md
```

把能查的规则装上牙，并挂进 Claude Code：

```bash
python3 hands-off/rule-teeth/rule_teeth.py init /你的项目 --hook
```

AI 到底常违反哪几条？拿你自己的 Claude Code 会话重放一遍（只读）：

```bash
python3 hands-off/rule-teeth/rule_teeth.py audit /你的项目 --out 实测榜.md
```

不想要了，一条命令摘掉：

```bash
python3 hands-off/rule-teeth/rule_teeth.py unhook /你的项目
```

运行时引擎会复制进你项目的 `.rule-teeth/`，克隆下来的仓库只在体检、装牙、摘牙时用得到。

不会敲命令也没关系，直接跟 Claude Code 说：「用 rule-teeth 给这个项目做一次规则体检，把报告给我看」。

## 输出样例

作者自己的私有仓库（16 个子项目，作者本人不写代码）的体检，一句话结论：

> **一句话**：51 条规则，**42 条从来没被机器检查过**（82%），只靠 AI 自觉；4 条现在就能装上牙。
>
> **第一条发现**：每个 session 开工前固定要读约 14,945 token 的规则；根文件只占 3,251，另外 4 个文件是规则里写着「每次都读」带进来的。

花一个下午把规则搬到该在的层之后：每个 session 3,737 token，一条规则都没删。怎么做的：[LAYERS.zh.md](LAYERS.zh.md)。

同一个仓库跑 `audit`（18 天、163 个会话文件，重放了 8,209 次改文件 / 跑命令和 682 条回复）：最常被违反的是「回复开头」那条，279 条回复没照做 —— 它最后一天才装上牙，装上以后拦了 3 次；2 条能查的规则从没触发；6 条还没装牙。规则验真查了 80 个路径 / 命令：1 个失效，1 个没写在哪个目录下跑（未能判断）。

两个公开项目的 AGENTS.md 也体检过（只读，只给位置和分类，不转载原文）：[vercel/next.js](demo/public/vercel-next.js_AGENTS.md_checkup.md)（33KB、550 行，是官方建议 200 行的近 3 倍，每个 session 约 9,400 token；71 条规则里 8 条能直接变成 hook）、[openai/openai-agents-python](demo/public/openai-agents-python_AGENTS.md_checkup.md)。

## 三类规则

| 分类 | 意思 | 例子 |
|---|---|---|
| **能自动检查** | 规则里写出了机器认得的东西：反引号里的写法 / 命令 / 文件、明确的行数、密钥、回复开头 | 「单文件不得超过 1500 行」「不许用 `requests.post`，只能经 `llm.py`」「Do NOT mark PRs as ready (`gh pr ready`)」 |
| **要人判断** | 说得清楚，但要读懂意思才能判断。报告会写原因，能拆出可查部分的，教你怎么拆 | 「每个重大决策必须跑一次反证」 |
| **太模糊** | 连人都没法判断算不算做到了。报告会给改写建议 | 「代码要优雅」「Keep commit messages concise」 |

拿不准时，rule-teeth **宁可判成「要人判断」，也不生成一条会乱拦的检查**。一个老喊「狼来了」的检查，很快就没人理了。

## 能长出哪几种牙

| 牙 | 从什么规则来 | 什么时候咬 |
|---|---|---|
| 行数 / 体量上限 | 「单文件不超过 N 行」「根文件 10KB / 200 行」 | 改文件前、结束前 |
| 禁用写法 | 「不许用 `X`」「`X` 只能出现在 `Y` 里」 | 改文件前、结束前 |
| 不许写密钥 | 「API key 不硬编码」「Never commit secrets」（内置 8 种密钥格式） | 改文件前、结束前 |
| 不许改的文件 | 「不要改 `vendor/`」「Do not edit `docs/ja`」 | 改文件前 |
| 不许跑的命令 | 「Do NOT `gh pr ready`」「用 `pnpm` 不用 `npm`」「commit 里不许写 "Generated with …"」 | 跑命令前 |
| 不许传给某个 MCP 工具的内容 | 「不许让 AI 给数据库工具发不带 `WHERE` 的 `DELETE`」—— **手写**，`init` 不会自动生成（[见下](#管住-mcp-工具forbid_tool_input)） | 指定的工具运行前 |
| 结束前必须跑通的命令 | 「改完代码跑 `pytest`」 | 结束前（**默认关**，要你亲手打开） |
| 回复开头 | 「回复都以 "John —" 开头」 | 结束前 |
| 每次必读预算 | 「每个 session 固定要读的规则合计不超过 4,000 token」—— 根文件、`@导入`、不带 paths 的 `.claude/rules`、规则里写着「每次都读」的文件一起算 | 改这条链上的任何文件前、结束前 |

## 管住 MCP 工具（`forbid_tool_input`）

`init` 装的 hook 只匹配 `Write|Edit|Bash`。MCP 工具（数据库、GitHub、文件服务）是另一个工具名，上面所有检查都看不到它 —— Bash 里会被拦的事，AI 换成 MCP 就能做。`forbid_tool_input` 专门看**某个指定工具的入参**。它要你判断「哪个工具、什么算太危险」，所以是**手写**进 `.rule-teeth/rules.json` 的，`init` 不会生成；而且 **`.claude/settings.json` 里这条 hook 的 matcher 必须写上这个工具名**，比如 `Write|Edit|Bash|mcp__supabase__execute_sql|mcp__supabase__apply_migration`。Claude Code 是开会话时读 hook 配置的，所以改了 matcher 要新开的会话才带上。

| 键 | 意思 |
|---|---|
| `type` | `"forbid_tool_input"` |
| `tools` | 工具名，`fnmatch` 通配，区分大小写：`["mcp__supabase__execute_sql"]`、`["mcp__supabase__*"]` |
| `field` | 扫入参里的哪个键（比如 `"query"`）。入参里没有这个键（工具改了名）→ 扫**全部**字符串值，免得改名后规则悄悄失效 |
| `sql_deny` | 内置的 SQL 类别：`truncate`、`drop_table`、`drop_schema`、`drop_database`、`delete_no_where`、`update_no_where` |
| `patterns` | 你自己的正则。默认匹配原始字符串；加 `"normalize": "sql"` 则匹配「去掉注释、字符串内容、子查询之后的每条 SQL 语句」 |
| `rule` / `source` / `enabled` / `note` | 和别的检查一样 |

没写 `tools`、没写类别也没写正则、类别名拼错、正则编译不过，都会在加载 `rules.json` 时直接报错 —— 一条悄悄什么都不拦的规则，比大声报错更糟。

```json
{"id": "RT13", "type": "forbid_tool_input", "tools": ["mcp__supabase__execute_sql"], "field": "query",
 "sql_deny": ["truncate", "drop_table", "drop_schema", "drop_database", "delete_no_where", "update_no_where"],
 "rule": "SQL 工具不许清表删表", "source": "CLAUDE.md:12"}
{"id": "RT14", "type": "forbid_tool_input", "tools": ["mcp__supabase__apply_migration"], "field": "query",
 "sql_deny": ["truncate", "drop_schema", "drop_database", "delete_no_where", "update_no_where"],
 "rule": "迁移里可以删表，但不许清数据"}
```

**它怎么读 SQL。** 不连数据库，什么都不执行，只把文字拆成一条条语句：`--` 和 `/* */` 注释（含嵌套）换成空格，`'…'` / `E'…'` / `"…"` 的内容抹掉，`;` 分句，每个 `( … )` 当成一条独立语句来判断。所以字符串、注释里的关键字不算（`SELECT 'DROP TABLE x'` 放行），子查询里的 `WHERE` 不算外层语句的 `WHERE`（`UPDATE t SET a = (SELECT … WHERE …)` 照拦），`WITH d AS (DELETE FROM t RETURNING *)` 也藏不住。`DO $$ … $$` 块和函数体当代码扫。单独一个 `WHERE true` / `WHERE 1=1` 算没有 `WHERE`。`ON CONFLICT … DO UPDATE SET`、`FOR UPDATE`、`ON DELETE CASCADE`、`GRANT UPDATE ON …`、`CREATE TRIGGER … AFTER DELETE` 都不是在改数据，放行。拦截信息只说类别（「DELETE 没有 WHERE」），**不回显 SQL 原文** —— 里面可能有用户数据。

**收紧或放松。** 每个类别就是 `sql_deny` 里的一项。想让迁移里的 `DROP TABLE` 也拦，把 `"drop_table"` 加进 RT14 的列表；内置类别没覆盖的，加一条正则：`"patterns": ["\\bDROP\\s+COLUMN\\b"], "normalize": "sql"`；想放过某一类，把它从列表里去掉；整条关掉用 `"enabled": false`。

**它拦不住的**（这是护栏不是墙 —— 它看的是 AI 写了什么，不是数据库会怎么执行）：
- 运行时拼出来的 SQL：`EXECUTE 'DELETE FROM t'`、`format(…)`、字符串拼接。
- `MERGE … WHEN MATCHED THEN DELETE`；六类之外的破坏性语句（`DROP COLUMN`、`ALTER … TYPE`、`DROP FUNCTION`、`REVOKE` ……），要拦得自己加正则。
- 对每一行都成立、却不是单独 `true` / `1=1` 的 `WHERE`（`WHERE id = id`、`WHERE a = 1 OR true`）。
- 走别的路到达数据库的操作（命令行、控制台、Edge Function，或不在 matcher 里的别的工具名）。
- 也有误报：列名恰好叫 `delete` / `update`；函数**定义**里带着不带 `WHERE` 的 `DELETE`，虽然还没执行也会被拦。

## 规则越写越多，token 怎么不涨

一条规则的成本 = 被加载的次数 × 长度；它管不管用 = 违反那一刻它在不在 AI（或机器）眼前。所以每条规则放进还来得及生效的最便宜那层：**牙**（违反前 0 token，拦截信息里带着规则原文）→ **根**（每个 session）→ **路径**（带 `paths:` 的 `.claude/rules`、子目录 `CLAUDE.md`）→ **触发**（「做 Y 时读 X」、skill）→ **档案**。项目越长越大，靠「每次必读预算」这颗牙自动保持。方法、给新规则找位置的三问、改造前后的数字：[LAYERS.zh.md](LAYERS.zh.md)。

## 安全设计

- **不替你乱跑命令。** 从文档里抽出来的「要跑的命令」一律默认关着。别人的仓库里如果写着「每次提交前跑 `curl 某网址 | sh`」，rule-teeth 不会自动执行它 —— 你在 `.rule-teeth/rules.json` 里亲手打开才算数。
- **AI 不能给自己松绑。** hook 会拦下 AI 对 `.rule-teeth/` 和 `.claude/settings*.json` 的修改。规则松不松，人说了算。
- **只拦新增的问题（棘轮）。** 文件里早就有的老问题，不拦 AI 这次无关的修改；但不许变得更糟。
- **老问题登记成欠条，不是豁免。** 装牙时现有的违规登记成「暂缓」，默认 30 天到期，到期自动重新算违规。
- **坏了要说出来，但不挡路。** `rules.json` 写坏了，hook 显式报错（Claude Code 里看得见），但不拦你干活。
- **不死循环。** 结束前的检查对同一批违规最多连拦 2 次，之后放行并明确提示「还没解决，请人来看」；Claude Code 自己也有连续 8 次的上限。
- **会话记录只读不改。** `audit` 只用只读方式打开会话记录，不跑任何命令，不调模型。找不到会话记录、目录是空的，直接报错，不出一份「一切正常」的空报告；读不懂的行数出来写进报告。
- **未能判断 ≠ 通过。** 规则验真什么都不执行，只看磁盘、PATH、`package.json` 和 Makefile；这些看不出来的，就老实写「未能判断」。

## 和 Claude Code 自带的 `/doctor prompt-audit` 有什么不一样

官方的 prompt-audit 用 AI 读你的规则文件，找过时、冲突、引用了不存在文件的地方，给出修改建议 —— 它让规则**写得更好**。rule-teeth 不跟它抢：它把规则变成**机器执行的检查**，挂进 hook，让规则**真的被遵守**。官方文档自己也说：CLAUDE.md 是上下文，不是强制配置；必须在某个时间点执行的事，应该写成 hook。rule-teeth 就是把这件事自动化。两个可以一起用。

## 已知局限（v0.2）

- 「写着每次都读」靠中英文常见说法认（「每个 session 都读」「开工前先读」、标题写着「每个 session 都读」、read X at the start of every session 等），别的说法会漏算；「」或引号里提到的算谈论规则，不算规则。
- 只认内置的 9 种牙（第 9 种 `forbid_tool_input` 要手写：`init` 不会生成它，`audit` 也不重放它）。「函数不超过 50 行」要按语言做语法分析，还没做。
- 走 Bash 写文件（`cat > a.py`）绕过了改文件前的检查；结束前的全量检查会补上，但那时文件已经写了。
- 「已有检查」是线索不是证明：测试 / CI / hook 文件里出现了规则编号或它独有的写法就算。
- 只读一份别人的 AGENTS.md 时，看不到他们的仓库，所以不说「从没被检查过」，只说「规则文件里没写明由谁兜底」。
- `audit` 拿的是**现在**的规则去重放以前的会话。Write 按整个新文件算（会话记录里看不出改之前长什么样）；Edit 只看新增的那段；行数上限只有 Write 能算；用 Bash 写进文件的内容看不到。「结束前必须跑通的命令」和「每次必读预算」没法重放，只数真拦过几次。
- 规则验真查的是这台电脑。PATH 里找不到的程序标「未能判断」（AI 的 shell 可能 PATH 不同）；`python -m x` 只在这台电脑的 Python 里找。

## 开发

```bash
python3 tests/run_all.py
```

只用标准库 unittest，要 git（会造临时仓库）。其中 `tests/test_hook.py` 用子进程模拟 Claude Code 调 hook，验证退出码 2、提示文字、防死循环；`tests/test_audit.py` 拿 `tests/fixtures/sessions/` 里的假会话记录重放。

## 文件

```
rule_teeth.py          命令行入口
ruleteeth/
  engine.py            ★ 运行时：检查 + hook。单文件、只用标准库，init 时原样复制进你的项目
  parse.py             找规则文件、切成候选片段
  classify.py          判断是不是规则、分三类、生成检查
  checkup.py           体检：没检查过的、该下沉的、超体量的、死指针、写两遍的、检查覆盖不全的
  refs.py              规则验真：规则点名的路径和命令在不在（在 / 失效 / 未能判断）
  audit.py             实测榜：重放本机 Claude Code 会话记录，按真实违规次数给规则排先后
  report.py            写成 Markdown 报告（中 / 英）
  install.py           写 .rule-teeth/、挂 / 摘 hook
  model.py             数据类型（每个概念只定义一次）
tests/                 自带测试
LAYERS.zh.md           分层方法：五层、三问、每次必读预算
demo/                  两个公开项目 AGENTS.md 的体检报告
```

## 系列：hands-off（敢放手）

专防「放手让 AI 干活之后会出的事故」的小工具，每个都是单文件、只用标准库、不调模型。

| 工具 | 防什么 |
|---|---|
| **rule-teeth 规则长牙** | `CLAUDE.md` / `AGENTS.md` 里写了、AI 却悄悄不听的规则 |
| [send-fuse 发送保险丝](../send-fuse/README.zh.md) | 不该发出去的邮件和消息 |
| [tripwire 注入绊线](../tripwire/README.zh.md) | 网页里一行小字，骗 AI 把你的密钥发出去 |
| [blackbox 黑匣子](../blackbox/README.zh.md) | 「它昨晚到底干了什么？」—— 人话时间线 + 一键撤销 |
| [brake 刹车](../brake/README.zh.md) | AI 卡在死循环里烧一整晚的钱 |
| [local-first 本地优先](../local-first/README.zh.md) | 本机模型就能干的活，却花钱送去云端 |

⭐ 觉得有用，[给 hands-off 点个星](https://github.com/JohnSUNLei/hands-off)，系列更新第一时间看到。有问题、发现 bug，或者想给你团队的 AI 也装上这类护栏？[开个 issue](https://github.com/JohnSUNLei/hands-off/issues)。

[MIT 许可证](LICENSE)。
