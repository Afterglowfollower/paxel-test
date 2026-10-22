# Paxel Test Local

这是一个**独立的本地复现原型**，用于分析 Claude Code / Codex / Hermes / DeepSeek harness / opencode 会话、关联本地 Git 证据，并生成 HTML 报告。它不是 YC Paxel 官方客户端，也不保证与官方分数相同。

## 前置

- Node.js 18+
- Python 3.9+
- Git
- 可选：`zstandard`（读取 DeepSeek harness 的 `.zstd` 日志；`python3 -m pip install zstandard`）
- 可选：`GLM_API_KEY`（生成模型评价；没有 Key 时只出本地证据报告）

## 使用方式

### 1. 全量：一条命令查全部

```bash
npx --yes github:Afterglowfollower/paxel-test --all-local
```

范围：

- Claude Code（`~/.claude/projects`）
- Codex（`~/.codex/sessions`）
- Hermes（`~/.hermes/state.db`，SQLite 只读）
- DeepSeek harness（`$DSH_HOME/sessions`、`~/.dsh/sessions`、`~/dsh-home/sessions`）
- opencode（`~/.local/share/opencode/opencode*.db`，SQLite 只读）

`--all-local` 会按每个会话的 `cwd` 找到对应 Git 仓库，收集时间窗内的本地提交，并按仓库分组、关联片段。不在 Git 仓库里的会话（例如临时工作目录）不会产生提交证据。

默认输出到当前目录的 `paxel-report/report.html`。不带 `--all-local` 时，`npx` 入口默认只分析 Claude Code 最近 90 天。

### 2. 单项目

```bash
npx --yes github:Afterglowfollower/paxel-test --project /绝对路径/你的项目
```

只分析 `cwd` 在该仓库下的会话，加该仓库的本地 Git / PR ref / 导出的 diff。可和 `--sources` 组合：

```bash
npx --yes github:Afterglowfollower/paxel-test \
  --project /绝对路径/你的项目 --sources opencode
```

如果 PR diff 已经在本地导出，可加 `--pr-diff-dir /目录`（文件名为 `pr-42.diff`），或用 `--base-ref main` 读取本地 `refs/pull/N/head`。工具不会为了补 diff 自动联网。

### 3. 单来源（单个 harness）

```bash
# 只分析 Hermes
npx --yes github:Afterglowfollower/paxel-test --all-local --sources hermes

# 只分析 opencode
npx --yes github:Afterglowfollower/paxel-test --all-local --sources opencode

# 多选
npx --yes github:Afterglowfollower/paxel-test --all-local --sources claude,codex
```

可选来源：`claude`、`codex`、`hermes`、`deepseek`、`opencode`。

### 4. 常用参数

| 参数 | 作用 |
| --- | --- |
| `--all-local` | 五种来源 + 逐仓库 Git 提交 |
| `--project PATH` | 单个项目；读取该项目下的会话和本地 Git |
| `--sources LIST` | 逗号分隔的来源过滤，默认全部 |
| `--days N` | 最近 N 天，默认 90 |
| `--output-dir PATH` | 报告目录；Node 入口默认 `./paxel-report`，Python 入口默认 `./paxel-reproduction` |
| `--score` / `--no-score` | 强制模型评价 / 强制只做本地证据报告（`--no-score` 是 Node 入口参数；Python 入口不写 `--score` 即可） |
| `--model NAME` | GLM 模型名，默认 `glm-5.3` |
| `--concurrency N` | 模型调用并发数，默认 4；`1` 为串行 |
| `--max-parts N` | 跳过需要超过 N 段叙事的超大会话，默认 0（不限制） |
| `--prompt-profile local\|v15-v5` | prompt 基线，默认 `local` |
| `--claude-dir`、`--codex-dir`、`--hermes-db`、`--dsh-dir`、`--opencode-dir` | 指定各来源位置 |
| `--help` | 查看完整参数 |

也可以直接用 Python 入口：

```bash
python3 python/paxel_reproduction.py --all-local --days 90 --score --concurrency 4 \
  --output-dir /你的/私有输出目录
```

## 模型评价

设置 `GLM_API_KEY` 后，同一条命令会自动调用 GLM：先提取会话叙事和工作片段证据，再基于全部已分析片段生成**一份针对人的综合评价**。报告层次包括：一句总体判断、构建者类型、AI 协作方式、做过什么、决策模式、优势、成长点、五维评价依据和判断边界。逐片段结果只留在 JSON 里供审计，不逐条呈现在报告页面。

```bash
export GLM_API_KEY='在你自己电脑上设置的新 Key'
npx --yes github:Afterglowfollower/paxel-test --all-local
```

没有 Key 时仍会生成**未评分**的本地覆盖报告，不会用会话数量或工具调用次数冒充能力评价。使用 `--no-score` 可强制只在本机处理；使用 `--score` 可明确要求模型评价。请勿把 Key 写入命令行参数、仓库或聊天记录。

## 报告什么时候出现

| 模式 | `report.html` 出现时机 | 内容 |
| --- | --- | --- |
| `--no-score`（或无 Key） | 本地解析完立即写，通常几秒到几十秒 | 证据/覆盖报告；人物评价留空 |
| `--score` | 所有模型阶段结束后写 | 完整人物报告 |
| 中途中断 | 没有最终 HTML，但中间 JSON 保留 | `session_narratives.json`、`episode_scores.json` 等可续跑 |

`--score` 期间每完成一个阶段会增量写 JSON；`report.html` 是最后一次性生成的。想先立刻看本地覆盖情况，可以先跑 `--no-score`，之后再跑 `--score` 覆盖成完整报告。

## Prompt profile

默认使用本仓库独立编写的 prompt（`--prompt-profile local`）。如果要对齐公开客户端里的三处 prompt，先在仓库根目录运行：

```bash
bash tools/extract-v15-prompts.sh
```

它会从公开镜像 `ghcr.io/yc-software/paxel-client:sha-07dc3b878811`（应用版本 `0.3.48.2`，2026-07-02）提取评分 v15、会话叙事 v5、叙事合并 v5、决策分类 v5，然后可以用：

```bash
npx --yes github:Afterglowfollower/paxel-test --prompt-profile v15-v5
```

`0.3.48.2` 是**最后一个还带 prompt 正文的公开客户端**。线上 `0.3.83.2`（`latest`）只声明 prompt id（`episode_scoring v16`、`session_narrative v6` 等），正文由服务端注入，公开镜像里没有。人物级综合 prompt 也不在公开客户端里，所以两个 profile 都用本仓库的本地实现；`v15-v5` 表示“三处客户端 prompt 用官方 v15/v5，人物综合仍是本地”，不是官方同分复刻。

prompt 正文不随仓库分发（见 `python/prompts/v15-v5/NOTICE.md`），只在本机生成和使用。官方 v15/v5 prompt 是英文原生，所以这个 profile 下中间叙事和片段事实是英文；报告页面和人物综合仍是中文。这是对原版 prompt 的忠实行为，不是语言 bug。

离线检查：

```bash
python3 tools/smoke-prompt-profiles.py
```

## 性能与并发

本地是“通用 API 客户端 + 有限并发”，不是 YC 服务端那条并行流水线，通常会慢一些：

- 默认 `--concurrency 4`；API 允许时可以调到 8，遇到限流再降回 1；
- 超大会话用 `--max-parts 2` 或更小，避免单段调用超时；
- 范围太大时先缩 `--days 30` 或 `--sources hermes`；
- 重跑会复用已完成的叙事/评分缓存，不会全部重算；
- 只想看本地覆盖，用 `--no-score`，通常几十秒内出报告；
- `glm-5.3` 是 reasoning 模型，换更快的模型（例如 `glm-4.5-flash`）会更快，但评分口径会变，建议先小样本对比。

## 数据边界

| 模式 | 外部请求 | 说明 |
| --- | --- | --- |
| `--no-score` | 无模型请求 | 全程本地：读会话、读 Git、写报告；不向 Paxel / YC / GLM 发送内容 |
| `--score` | 调用 GLM API | 把截断后的会话叙事、片段证据、决策候选发给 GLM；服务商留存策略由服务商决定 |
| Git | 只读本地 | 不 fetch、不 push、不拉远端 PR |
| Hermes / opencode | SQLite 只读 | 不修改数据库 |

发送给 GLM 前会做正则脱敏（token、key、路径、邮箱等），但这是**尽力而为，不是保证**。输出目录里的 `report.html`、`evidence.json`、`narrative_inputs.json` 都可能含私人会话，**不要提交、不要外发**。

## 报告与局限

所有输出保存在本机指定的目录，包括 `report.html`、`person_profile.json`、会话证据、候选决策、工作片段和评分输入 JSON。工具本身不会上传到 Paxel 或众测平台。

目前已用原版 Paxel 镜像在断网条件下对照会话发现、片段关联和决策候选阶段。Claude Code 的 41 份文件都被发现，双方各生成 35 个工作片段；原版决策候选 220 条，本工具 216 条。也对照了用户已生成的官方 V3 报告页面：原版的人物叙事远比五个分数丰富。本工具参考其报告层次、公开的五维评分锚点和四类决策分类，但原版服务端的跨会话叙事合成、人物标签生成与校准代码未公开；这些部分是独立实现，不能称为官方同分复刻。公开客户端另有 49 条决策模式目录，本工具尚未逐条映射。逐层状态见 [逆向对照](REVERSE_ENGINEERING.md)。

当前能力边界：

- 不支持 Cursor；opencode 已支持；
- `--all-local` 的 Git 提交只来自本地仓库，不联网拉 PR；
- 不执行测试、不验证部署，测试通过与否只依据可见日志；
- 原版 v16/v6 prompt 和最终人物合成在服务端，公开 artifact 拿不到；
- `--score` 的人物综合是本地实现，不是官方同分。

此仓库只包含独立复现的 CLI、本地分析代码和 prompt 提取脚本；不包含用户会话、私密报告、官方客户端镜像或 API Key。v15-v5 的 prompt 正文由本地脚本生成，并被 git 忽略。
