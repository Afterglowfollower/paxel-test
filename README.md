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

要把本机的 Claude Code、Codex 和 Hermes 编程会话一起纳入报告：

```bash
npx --yes github:Afterglowfollower/paxel-test --all-local
```

`--all-local` 读取 `~/.codex/sessions`、`~/.claude/projects` 和 `~/.hermes/state.db`；Hermes 数据库仅以 SQLite 只读模式打开，默认只纳入带工作目录的 CLI/one-shot/ACP 会话。可用 `--codex-dir`、`--claude-dir`、`--hermes-db` 指定其他位置。跨项目汇总模式暂不关联各项目 Git 提交；需要 Git 证据时使用 `--project`。Cursor 暂未接入，等有脱敏导出样本后再适配。

## 模型评价

设置 `GLM_API_KEY` 后，同一条命令会自动调用 GLM，为会话生成叙事并为工作片段生成评价：

```bash
export GLM_API_KEY='在你自己电脑上设置的新 Key'
npx --yes github:Afterglowfollower/paxel-test
```

没有 Key 时仍会生成**未评分**的本地证据报告。使用 `--no-score` 可强制只在本机处理；使用 `--score` 可明确要求模型评价。请勿把 Key 写入命令行参数、仓库或聊天记录。模型评价模式会把报告所需的会话片段发送到 GLM API；请先确认你有权处理这些会话。

## 报告与局限

所有输出保存在本机指定的目录，包括 `report.html`、会话证据、候选决策、工作片段和评分输入 JSON。**这些文件可能含私人会话或项目内容，不要直接外发。** 工具本身不会上传到 Paxel 或众测平台。

目前已用原版 Paxel 镜像在断网条件下对照会话发现、片段关联和决策候选阶段。Claude Code 的 41 份文件都被发现，双方各生成 35 个工作片段；原版决策候选 220 条，本工具 216 条。模型确认的决策与五维分数尚未做到原版对齐，所以报告适合作为人工复核的预评估。

此仓库只包含独立复现的 CLI 和本地分析代码，不包含用户会话、私密报告、官方客户端镜像或 API Key。
