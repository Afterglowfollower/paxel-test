#!/usr/bin/env node

const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
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
默认使用 GLM 生成模型评价；也可通过 --provider ark 使用火山方舟。加 --no-score 只生成本地证据报告。

选项:
  --project PATH       只分析一个项目，可同时读取该项目的 Codex/Claude 会话和 Git
  --claude-dir PATH    自定义 Claude Code 会话目录
  --codex-dir PATH     自定义 Codex 会话目录
  --all-local          汇总 Claude Code、Codex、Hermes、DeepSeek harness 和 opencode 会话
  --sources LIST       只分析指定来源：claude,codex,hermes,deepseek,opencode（默认全部）
  --max-parts N        跳过需要超过 N 段叙事的超大会话（0 = 不限制）
  --hermes-db PATH     Hermes SQLite 数据库，默认 ~/.hermes/state.db
  --dsh-dir PATH       DeepSeek harness 的 sessions 目录
  --opencode-dir PATH  opencode 数据目录或单个 opencode*.db
  --concurrency N      模型调用并发数，默认 4；1 = 串行
  --days N             分析最近 N 天，默认 90
  --output-dir PATH    报告目录，默认 ./paxel-report
  --score              强制调用所选服务商；需要对应 API Key
  --provider NAME      glm（默认）或 ark（火山方舟）
  --api-key-file PATH  从指定文件读取 key（默认 ~/.config/paxel-test/{provider}_api_key）
  --prompt-profile P   local（默认）或 v15-v5（需先运行 tools/extract-v15-prompts.sh）
  --no-score           只生成本地证据报告，不调用模型
  --model NAME         模型 ID；GLM 默认 glm-5.3，方舟需指定已开通的模型 ID

报告在输出目录的 report.html。完整底层参数见 python/paxel_reproduction.py。
`);
  process.exit(0);
}

const args = [];
let project = null;
let noScore = false;
let hasOutputDir = false;
let hasScore = false;
let apiKeyFile = null;
let provider = 'glm';
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
  } else if (arg === '--provider') {
    if (!raw[i + 1]) {
      console.error('--provider 需要 glm 或 ark');
      process.exit(2);
    }
    provider = raw[++i];
    args.push('--provider', provider);
  } else if (arg.startsWith('--provider=')) {
    provider = arg.slice('--provider='.length);
    args.push(arg);
  } else if (arg === '--api-key-file') {
    if (!raw[i + 1]) {
      console.error('--api-key-file 需要一个文件路径');
      process.exit(2);
    }
    apiKeyFile = raw[++i];
    args.push('--api-key-file', apiKeyFile);
  } else if (arg.startsWith('--api-key-file=')) {
    apiKeyFile = arg.slice('--api-key-file='.length);
    args.push(arg);
  } else {
    if (arg === '--output-dir') hasOutputDir = true;
    if (arg === '--score') hasScore = true;
    args.push(arg);
  }
}
if (!['glm', 'ark'].includes(provider)) {
  console.error('--provider 需要 glm 或 ark');
  process.exit(2);
}

function defaultApiKeyFile() {
  const base = process.env.XDG_CONFIG_HOME || path.join(os.homedir(), '.config');
  return path.join(base, 'paxel-test', `${provider}_api_key`);
}

function apiKeyAvailable() {
  const envName = provider === 'ark' ? 'ARK_API_KEY' : 'GLM_API_KEY';
  if (process.env[envName] && process.env[envName].trim()) return true;
  const candidates = apiKeyFile ? [apiKeyFile] : [defaultApiKeyFile()];
  for (const file of candidates) {
    try {
      if (fs.existsSync(file) && fs.readFileSync(file, 'utf8').trim()) return true;
    } catch (error) {
      // Unreadable candidates are treated as absent; the Python resolver will
      // produce the final error if none can be read.
    }
  }
  return false;
}

if (project) {
  args.unshift(project);
} else if (!args.includes('--all-claude') && !args.includes('--all-local')) {
  args.unshift('--all-claude');
}
if (!hasOutputDir) {
  args.push('--output-dir', path.resolve(process.cwd(), 'paxel-report'));
}
if (noScore) {
  if (hasScore) {
    console.error('--score 和 --no-score 不能同时使用');
    process.exit(2);
  }
  args.push('--no-score');
} else {
  if (!apiKeyAvailable()) {
    const envName = provider === 'ark' ? 'ARK_API_KEY' : 'GLM_API_KEY';
    console.error(`模型评价需要 ${envName}。可以：\n` +
      `  1) 设置 ${envName} 环境变量\n` +
      `  2) python3 tools/set-api-key.py --provider ${provider}\n` +
      '  3) --api-key-file /path/to/key\n' +
      '只想生成本地证据报告，加 --no-score。');
    process.exit(2);
  }
  if (!hasScore) {
    args.push('--score');
    hasScore = true;
  }
}

const python = process.env.PAXEL_PYTHON || 'python3';
console.log(hasScore ? `正在生成模型评价报告。会话片段将发送到 ${provider === 'ark' ? '火山方舟' : 'GLM'} API。`
                     : '正在生成本地证据报告（未调用模型）。');
const result = spawnSync(python, [cli, ...args], { stdio: 'inherit', env: process.env });
if (result.error) {
  console.error(`无法启动 ${python}: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status == null ? 1 : result.status);
