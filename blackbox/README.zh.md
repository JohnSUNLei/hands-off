# blackbox 黑匣子

**你的 AI 干了一整夜，到底干了什么？打开一页就知道；哪一步干坏了，一条命令就撤销。**

blackbox 是给 AI 编程助手装的飞行记录仪：它跑过的每条命令、改过的每个文件（包括用 shell 命令改的）、读过的每个网页，排成一条人话时间线；每个文件都留着改前改后，所以坏掉的那一步可以单独改回去，这一夜别的活一点不丢。

> 属于 [**hands-off（敢放手）**](https://github.com/JohnSUNLei/hands-off) 系列 —— 让 AI 替你干活也不出事的几个本地小工具。

![blackbox 演示：第二天早上，时间线指出把所有价格清零的那一步，一条命令撤销](promo/blackbox_demo.gif)

```bash
curl -fsSLo .blackbox/blackbox.py --create-dirs https://raw.githubusercontent.com/JohnSUNLei/hands-off/main/blackbox/blackbox.py && python3 .blackbox/blackbox.py init --lang zh
```

一个 Python 文件，只用标准库。不调模型、不注册、不联网、不常驻。
目前通过 hooks 接 Claude Code。[English](README.md)

---

## 第二天早上

睡前你交代：「把结账代码整理一下，让测试通过」。早上它说：

> Done. Tests pass (4/4). I normalized the price data and removed old backups.

结果「整理价格数据」的意思是：所有价格都变成了 `0`，备份也没了。是哪条命令？几点？文件原来长什么样？还动了别的吗？

```bash
python3 .blackbox/blackbox.py report --open
```

![时间线页面](promo/blackbox_timeline.png)

```
$ python3 .blackbox/blackbox.py undo 6
撤销第 6 步 —— Normalize price fields —— `sed -i '' 's/"price": [0-9.]*/"price": 0/' data/prices.json`：
  恢复  data/prices.json
好了，第 6 步已撤销（记为第 12 步；`undo 12` 可以再改回来）。
```

第 4 步对 `checkout.py` 的正确修改、第 10 步新建的 `NOTES.md`，原样保留。

## 记录什么

| | |
|---|---|
| 你说了什么 | 每一句指令（`UserPromptSubmit`），时间线按它分组 |
| 它跑了什么 | 每条 shell 命令，带 AI 自己写的一句说明和输出的结尾；失败的标出来 |
| 它改了什么 | 新建、修改、删除的每个文件，**包括 shell 命令改的**（`sed -i`、`rm -rf`、脚本、生成器），带增删行数和完整 diff |
| 它看了什么 | 网页（`WebFetch`）、搜索、看过的文件（默认折叠）、用过的 MCP 工具、启动的助手 |
| 旁边发生了什么 | 两步之间被你、编辑器或别的程序改动的文件——不会算到 AI 头上，撤销时也不会被悄悄覆盖 |
| 它说了什么 | 每次停下来时的最后一句话 |

值得看一眼的步骤会标出来：删文件、命令失败、`rm -rf`、`git push` / `reset --hard` / `clean -f`、联网命令、写到项目外面。

## 原理

- `init` 把引擎复制到 `.blackbox/`，在 `.claude/settings.json` 里加六条 hook（别的配置一概不动），并给项目拍一张初始快照。
- 每次改文件或跑 shell 命令之前，记下项目当时的样子；之后对比差异。内容按版本只存一份（内容寻址、zlib 压缩），一整夜只占几 MB。`node_modules`、`.git`、`.venv`、构建目录和超过 2 MB 的文件跳过（可配置）。
- 它通过 Claude Code 公开文档里的 hook 接口记录——不去解析会话记录文件（那是内部格式，每个版本都可能变）。
- `undo N` 把第 N 步动过的每个文件恢复成第 N 步之前的样子：改过的还原、新建的删掉、删掉的找回。如果**后面**某一步又改过同一个文件，它会拒绝并告诉你是哪一步（加 `--force` 才强行覆盖）。撤销本身也记成一步，再 `undo` 一次就改回来。
- AI 可以读记录、跑 `list` / `show` / `report` / `undo`，但不能改、不能删 `.blackbox/`：飞行员能擦掉的黑匣子不叫黑匣子。

## 命令

```
python3 .blackbox/blackbox.py report [--open]   # 生成（并打开）.blackbox/timeline.html
python3 .blackbox/blackbox.py list [N]          # 在终端看最近 N 步
python3 .blackbox/blackbox.py show N            # 看某一步：输出和完整 diff
python3 .blackbox/blackbox.py undo N [--force]  # 把第 N 步改过的文件全部改回去
python3 .blackbox/blackbox.py uninstall         # 删掉我们的 hooks 和 .blackbox/（--keep-data 保留记录）
```

`init` 选项：`--lang zh`（中文时间线和提示）、`--local`（hooks 写进 `.claude/settings.local.json`）。
配置：`.blackbox/config.json` —— `max_file_mb`、`skip_dirs`、`output_chars`、`lang`。

记录（`.blackbox/steps.jsonl`、`objects/`）只留在你电脑上、不进 git；只有引擎和配置是要提交的。

## 和这些有什么不同

- **git**——只看得到你提交的东西，不知道哪一步、为什么、读了什么之后改的。
- **Claude Code 自带的检查点（`/rewind`）**——把代码和 / 或对话倒回到更早的某一句**指令**，只管 Claude 用编辑工具改过的文件；按官方文档，只被 shell 命令改过的文件不在追踪范围内。blackbox 看得到 shell 命令造成的改动，能只撤销其中一步、保留之后所有工作，并给你一页第二天早上能读（也能转给别人）的记录。
- **会话查看器**——给你看对话记录；blackbox 给你看**后果**（文件改前改后），而且能撤回。

## 局限

- 项目目录以外的改动（比如 `rm ~/某个文件`）会被标出来，但没有快照，撤销不了。
- 两条 shell 命令**并行**跑时分不清各自改了什么，改动可能同时记在两步上。
- 它撤销的是文件，不是世界：`git push` 出去的、发出去的邮件、写进数据库的、已经部署的，都撤不回来。时间线至少告诉你发生过。
- 超过 `max_file_mb`（默认 2 MB）的文件只记一笔，不保存内容。
- 目前接 Claude Code。其他 AI 工具需要一个小适配器，把同样的事件喂进来（欢迎 PR）。

已用 Claude Code 2.1.322 端到端验证：真实会话里的 `Write`、一条经 Bash 跑的 `sed -i` 和一条 `echo` 被记成三条人话步骤，`undo` 把 `sed` 改过的文件改了回去。

## 测试

```bash
python3 tests/run_all.py
```

26 个测试；夹具就是 Claude Code 发给 hooks 的原样 JSON（`tests/fixtures/`），在一个真实的临时项目上回放。

## 系列：hands-off（敢放手）

专防「放手让 AI 干活之后会出的事故」的小工具，每个都是单文件、只用标准库、不调模型。

| 工具 | 防什么 |
|---|---|
| [rule-teeth 规则长牙](../rule-teeth/README.zh.md) | `CLAUDE.md` / `AGENTS.md` 里写了、AI 却悄悄不听的规则 |
| [send-fuse 发送保险丝](../send-fuse/README.zh.md) | 不该发出去的邮件和消息 |
| [tripwire 注入绊线](../tripwire/README.zh.md) | 网页里一行小字，骗 AI 把你的密钥发出去 |
| **blackbox 黑匣子** | 「它昨晚到底干了什么？」—— 人话时间线 + 一键撤销 |
| [brake 刹车](../brake/README.zh.md) | AI 卡在死循环里烧一整晚的钱 |
| [local-first 本地优先](../local-first/README.zh.md) | 本机模型就能干的活，却花钱送去云端 |

⭐ 觉得有用，[给 hands-off 点个星](https://github.com/JohnSUNLei/hands-off)，系列更新第一时间看到。有问题、发现 bug，或者想给你团队的 AI 也装上这类护栏？[开个 issue](https://github.com/JohnSUNLei/hands-off/issues)。

[MIT 许可证](LICENSE)。
