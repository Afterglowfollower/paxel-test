#!/usr/bin/env node

const { spawnSync } = require('node:child_process');
const path = require('node:path');

const cli = path.resolve(__dirname, '..', 'python', 'paxel_reproduction.py');
const raw = process.argv.slice(2);

if (raw.includes('--help') || raw.includes('-h')) {
  console.log(`paxel-test: 在本机分析 AI 编程会话并生成 HTML 报告

用法:
  npx --yes github:Afterglowfollower/paxel-test
  npx --yes github:Afterglowfollower/paxel-test --all-local
  npx --yes github:Afterglowfollower/paxel-test --project /path/to/project

默认读取 ~/.claude/projects 下最近 90 天的全部 Claude Code 会话。
有 GLM_API_KEY 时自动生成模型评价；没有时生成未评分的证据报告。

选项:
  --project PATH       只分析一个项目，可同时读取该项目的 Codex/Claude 会话和 Git
  --claude-dir PATH    自定义 Claude Code 会话目录
  --codex-dir PATH     自定义 Codex 会话目录
  --all-local          汇总 Claude Code、Codex、Hermes 和 DeepSeek harness 会话
  --hermes-db PATH     Hermes SQLite 数据库，默认 ~/.hermes/state.db
  --dsh-dir PATH       DeepSeek harness 的 sessions 目录
  --days N             分析最近 N 天，默认 90
  --output-dir PATH    报告目录，默认 ./paxel-report
  --score              强制调用 GLM；需要 GLM_API_KEY
  --no-score           只生成本地证据报告，即使设置了 GLM_API_KEY
  --model NAME         GLM 模型名，默认 glm-5.3

报告在输出目录的 report.html。完整底层参数见 python/paxel_reproduction.py。
`);
  process.exit(0);
}

const args = [];
let project = null;
let noScore = false;
let hasOutputDir = false;
let hasScore = false;
for (let i = 0; i < raw.length; i += 1) {
  const arg = raw[i];
  if (arg === '--project') {
    if (!raw[i + 1]) {
      console.error('--project 需要一个目录');
      process.exit(2);
    }
    project = raw[++i];
  } else if (arg === '--no-score') {
    noScore = true;
  } else {
    if (arg === '--output-dir') hasOutputDir = true;
    if (arg === '--score') hasScore = true;
    args.push(arg);
  }
}

if (project) {
  args.unshift(project);
} else if (!args.includes('--all-claude') && !args.includes('--all-local')) {
  args.unshift('--all-claude');
}
if (!hasOutputDir) {
  args.push('--output-dir', path.resolve(process.cwd(), 'paxel-report'));
}
if (!noScore && !hasScore && process.env.GLM_API_KEY) {
  args.push('--score');
  hasScore = true;
}
if (noScore && hasScore) {
  console.error('--score 和 --no-score 不能同时使用');
  process.exit(2);
}
if (hasScore && !process.env.GLM_API_KEY) {
  console.error('模型评价需要 GLM_API_KEY；也可以使用 --no-score 生成本地证据报告。');
  process.exit(2);
}

const python = process.env.PAXEL_PYTHON || 'python3';
console.log(hasScore ? '正在生成模型评价报告。会话片段将发送到 GLM API。'
                     : '正在生成本地证据报告（未调用模型）。');
const result = spawnSync(python, [cli, ...args], { stdio: 'inherit', env: process.env });
if (result.error) {
  console.error(`无法启动 ${python}: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status == null ? 1 : result.status);
