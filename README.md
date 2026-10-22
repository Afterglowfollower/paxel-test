# Paxel Test Local

这是一个**独立的本地复现原型**，用于分析 Claude Code / Codex 会话、关联本地 Git 证据，并生成 HTML 报告。它不是 YC Paxel 官方客户端，也不保证与官方分数相同。

## 一行运行

需要 Node.js 18+、Python 3.9+ 和 Git。默认分析本机 `~/.claude/projects` 下最近 90 天的 Claude Code 会话：

```bash
npx --yes github:Afterglowfollower/paxel-test
```

命令结束后打开当前目录的 `paxel-report/report.html`。默认全量 Claude 模式会关联主会话与子代理，但**不汇总 Git 提交**。

要为一个具体项目关联 Codex、Claude Code、Hermes 会话及本地 Git 证据，可运行：

```bash
npx --yes github:Afterglowfollower/paxel-test --project /绝对路径/你的项目
```

如果 Claude Code 会话保存在别处，加 `--claude-dir /会话目录`。用 `--days 30` 指定时间范围，用 `--output-dir /私有目录` 指定报告位置。运行 `npx --yes github:Afterglowfollower/paxel-test --help` 可查看参数。

要把本机的 Claude Code、Codex、Hermes、DeepSeek harness 和 opencode 编程会话一起纳入报告：

```bash
npx --yes github:Afterglowfollower/paxel-test --all-local
```

`--all-local` 读取 `~/.codex/sessions`、`~/.claude/projects`、`~/.hermes/state.db`、DeepSeek harness 的 v4 会话日志和 opencode 的 SQLite 数据库。它还会按每个会话的 `cwd` 找到对应 Git 仓库，收集时间窗内的本地提交，并按仓库分组、关联片段；不在 Git 仓库里的会话（例如临时工作目录）不会产生提交证据。Hermes 数据库仅以 SQLite 只读模式打开，默认只纳入带工作目录的 CLI/one-shot/ACP 会话；已压缩归档的消息也会保留。DeepSeek harness 会扫描实际存在的 `$DSH_HOME/sessions`、`~/.dsh/sessions` 和 `~/dsh-home/sessions` 并按会话 ID 去重；可用 `--dsh-dir` 明确指定。压缩日志需要 Python `zstandard` 包；如未安装，先运行 `python3 -m pip install zstandard`。

只分析某个来源时加 `--sources hermes`（逗号分隔可多选，含 opencode）；超大会话可用 `--max-parts 2` 跳过，避免单次模型调用过慢；模型调用默认 4 路并发，可用 `--concurrency 1` 退回串行。可用 `--codex-dir`、`--claude-dir`、`--hermes-db`、`--dsh-dir`、`--opencode-dir` 指定其他位置。Cursor 暂未接入，等有脱敏导出样本后再适配。

这五种来源统一提取用户/助手消息、工具调用与结果，Codex 的同一逻辑会话会合并分段，Hermes 会保留压缩归档的可见消息，opencode 的 SQLite 以只读方式读取，DeepSeek harness 的运行时注入消息不会算作用户判断。DeepSeek 目前仅支持 v4 JSONL（含 `.zstd` 压缩）；它和 Hermes 的评分尚未与 Paxel 原版做逐阶段同分验证。

## 模型评价

设置 `GLM_API_KEY` 后，同一条命令会自动调用 GLM：先提取会话叙事和工作片段证据，再基于全部已分析片段生成**一份针对人的综合评价**。报告模仿原版的层次：一句总体判断、构建者类型、AI 协作方式、做过什么、决策模式、优势、成长点和五维评价依据。逐片段结果仅供本地审计，不会逐条呈现在报告中：

```bash
export GLM_API_KEY='在你自己电脑上设置的新 Key'
npx --yes github:Afterglowfollower/paxel-test
```

没有 Key 时仍会生成**未评分**的本地覆盖报告，不会用会话数量或工具调用次数冒充能力评价。使用 `--no-score` 可强制只在本机处理；使用 `--score` 可明确要求模型评价。请勿把 Key 写入命令行参数、仓库或聊天记录。模型评价模式会把报告所需的会话片段和中间摘要发送到 GLM API；请先确认你有权处理这些会话。

## Prompt profile

默认使用本仓库独立编写的 prompt（`--prompt-profile local`）。如果要对齐公开客户端里的三处 prompt，先在本机运行：

```bash
# 在仓库根目录运行
bash tools/extract-v15-prompts.sh
```

它会从公开镜像 `ghcr.io/yc-software/paxel-client:sha-07dc3b878811`（应用版本 `0.3.48.2`，2026-07-02）提取评分 v15、会话叙事 v5、叙事合并 v5、决策分类 v5，然后可以用：

```bash
npx --yes github:Afterglowfollower/paxel-test --prompt-profile v15-v5
```

`0.3.48.2` 是**最后一个还带 prompt 正文的公开客户端**。线上 `0.3.83.2`（`latest`）只声明 prompt id（`episode_scoring v16`、`session_narrative v6` 等），正文由服务端注入，公开镜像里没有。人物级综合 prompt 也不在公开客户端里，所以两个 profile 都用本仓库的本地实现；`v15-v5` 表示“三处客户端 prompt 用官方 v15/v5，人物综合仍是本地”，不是官方同分复刻。

prompt 正文不随仓库分发（见 `python/prompts/v15-v5/NOTICE.md`），只在本机生成和使用。

官方 v15/v5 prompt 是英文原生，所以这个 profile 下中间叙事和片段事实是英文；报告页面和人物综合仍是中文。这是对原版 prompt 的忠实行为，不是语言 bug。离线检查：

```bash
python3 tools/smoke-prompt-profiles.py
```

## 报告与局限

所有输出保存在本机指定的目录，包括 `report.html`、`person_profile.json`、会话证据、候选决策、工作片段和评分输入 JSON。**这些文件可能含私人会话或项目内容，不要直接外发。** 工具本身不会上传到 Paxel 或众测平台。

目前已用原版 Paxel 镜像在断网条件下对照会话发现、片段关联和决策候选阶段。Claude Code 的 41 份文件都被发现，双方各生成 35 个工作片段；原版决策候选 220 条，本工具 216 条。也对照了用户已生成的官方 V3 报告页面：原版的人物叙事远比五个分数丰富。本工具参考其报告层次、公开的五维评分锚点和四类决策分类，但原版服务端的跨会话叙事合成、人物标签生成与校准代码未公开；这些部分是独立实现，不能称为官方同分复刻。公开客户端另有 49 条决策模式目录，本工具尚未逐条映射。逐层状态见 [逆向对照](REVERSE_ENGINEERING.md)。

此仓库只包含独立复现的 CLI、本地分析代码和 prompt 提取脚本；不包含用户会话、私密报告、官方客户端镜像或 API Key。v15-v5 的 prompt 正文由本地脚本生成，并被 git 忽略。
