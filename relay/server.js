#!/usr/bin/env node
/**
 * Paxel relay: a small OpenAI-compatible Chat Completions proxy.
 *
 * Purpose: let other people run model-backed reports without ever holding your
 * upstream key. The key lives only in this process's environment; callers get a
 * revocable shared token plus hard spend limits.
 *
 * This is NOT a general-purpose proxy: the upstream URL is fixed by env, the
 * request shape is validated, and every call is metered.
 *
 * Required env:
 *   RELAY_TOKEN     comma-separated accepted caller tokens (rotate by editing)
 *   ARK_API_KEY     the real upstream key (never sent to callers)
 *
 * Optional env:
 *   RELAY_PORT               default 8787
 *   RELAY_UPSTREAM           default Ark Beijing chat completions
 *   RELAY_MODEL              force this model id for every call
 *   RELAY_MAX_TOKENS         hard clamp on max_tokens, default 8192
 *   RELAY_TIMEOUT_MS         upstream deadline, default 600000
 *   RELAY_DAILY_REQUESTS     global per-UTC-day cap, default 500
 *   RELAY_PER_IP_HOURLY      per-IP hourly cap, default 20
 *   RELAY_CONCURRENCY        in-flight cap, default 4
 *   RELAY_MAX_BODY_BYTES     request body cap, default 524288
 *
 * Run: node relay/server.js
 */
'use strict';

const http = require('node:http');

const UPSTREAM = process.env.RELAY_UPSTREAM || 'https://ark.cn-beijing.volces.com/api/v3/chat/completions';
const UPSTREAM_KEY = (process.env.ARK_API_KEY || '').trim();
const TOKENS = new Set(
  (process.env.RELAY_TOKEN || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean),
);
const FORCED_MODEL = (process.env.RELAY_MODEL || '').trim();
const PORT = Number(process.env.RELAY_PORT || 8787);
const MAX_TOKENS = Number(process.env.RELAY_MAX_TOKENS || 8192);
const TIMEOUT_MS = Number(process.env.RELAY_TIMEOUT_MS || 600000);
const DAILY_REQUESTS = Number(process.env.RELAY_DAILY_REQUESTS || 500);
const PER_IP_HOURLY = Number(process.env.RELAY_PER_IP_HOURLY || 20);
const CONCURRENCY = Number(process.env.RELAY_CONCURRENCY || 4);
const MAX_BODY_BYTES = Number(process.env.RELAY_MAX_BODY_BYTES || 524288);

if (!UPSTREAM_KEY) {
  console.error('relay: ARK_API_KEY is required (the upstream key stays in this process)');
  process.exit(1);
}
if (!TOKENS.size) {
  console.error('relay: RELAY_TOKEN is required (comma-separated caller tokens)');
  process.exit(1);
}
const upstreamUrl = new URL(UPSTREAM);
if (upstreamUrl.protocol !== 'https:' && !['127.0.0.1', 'localhost', '::1'].includes(upstreamUrl.hostname)) {
  console.error('relay: RELAY_UPSTREAM must be https (plain http is allowed only on loopback)');
  process.exit(1);
}

let dayKey = new Date().toISOString().slice(0, 10);
let dayCount = 0;
let inFlight = 0;
const perIp = new Map();

function allowCaller(ip) {
  const today = new Date().toISOString().slice(0, 10);
  if (today !== dayKey) {
    dayKey = today;
    dayCount = 0;
  }
  if (dayCount >= DAILY_REQUESTS) return 'daily_budget_exhausted';
  const now = Date.now();
  const window = (perIp.get(ip) || []).filter((stamp) => now - stamp < 3600_000);
  if (window.length >= PER_IP_HOURLY) {
    perIp.set(ip, window);
    return 'rate_limited';
  }
  window.push(now);
  perIp.set(ip, window);
  dayCount += 1;
  return null;
}

function clientIp(req) {
  const forwarded = String(req.headers['x-forwarded-for'] || '').split(',')[0].trim();
  return forwarded || req.socket.remoteAddress || 'unknown';
}

function send(res, status, payload) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
  });
  res.end(body);
}

function authorized(header) {
  const value = String(header || '');
  const match = /^Bearer\s+(.+)$/i.exec(value);
  if (!match) return false;
  const token = match[1].trim();
  // Constant-time-ish compare; token count is tiny so length leaks are moot.
  let ok = false;
  for (const candidate of TOKENS) {
    if (candidate.length === token.length) {
      let diff = 0;
      for (let i = 0; i < candidate.length; i += 1) diff |= candidate.charCodeAt(i) ^ token.charCodeAt(i);
      if (diff === 0) ok = true;
    }
  }
  return ok;
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on('data', (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error('body_too_large'));
        req.destroy();
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function forward(payload, signal) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  if (signal) signal.addEventListener('abort', () => controller.abort(), { once: true });
  try {
    return await fetch(UPSTREAM, {
      method: 'POST',
      headers: {
        authorization: `Bearer ${UPSTREAM_KEY}`,
        'content-type': 'application/json',
      },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timer);
  }
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || '/', 'http://relay.local');

  if (req.method === 'GET' && url.pathname === '/healthz') {
    send(res, 200, { ok: true, used_today: dayCount, daily_cap: DAILY_REQUESTS, in_flight: inFlight });
    return;
  }
  if (url.pathname !== '/v1/chat/completions') {
    send(res, 404, { error: { message: 'not_found' } });
    return;
  }
  if (req.method !== 'POST') {
    send(res, 405, { error: { message: 'method_not_allowed' } });
    return;
  }
  if (!authorized(req.headers.authorization)) {
    send(res, 401, { error: { message: 'invalid_relay_token' } });
    return;
  }
  if (inFlight >= CONCURRENCY) {
    send(res, 429, { error: { message: 'server_busy' } });
    return;
  }
  const refusal = allowCaller(clientIp(req));
  if (refusal) {
    send(res, 429, { error: { message: refusal } });
    return;
  }

  let payload;
  try {
    payload = JSON.parse(await readBody(req));
  } catch (error) {
    send(res, 400, { error: { message: error.message === 'body_too_large' ? 'body_too_large' : 'invalid_json' } });
    return;
  }
  if (!payload || !Array.isArray(payload.messages) || payload.messages.length === 0) {
    send(res, 400, { error: { message: 'messages_required' } });
    return;
  }

  // Rebuild the request: callers never control stream, model, or token budget.
  const upstreamPayload = {
    model: FORCED_MODEL || (typeof payload.model === 'string' ? payload.model : undefined),
    stream: false,
    max_tokens: Math.min(Number(payload.max_tokens) || MAX_TOKENS, MAX_TOKENS),
    messages: payload.messages,
  };
  if (!upstreamPayload.model) {
    send(res, 400, { error: { message: 'model_required' } });
    return;
  }

  inFlight += 1;
  const closed = new AbortController();
  req.on('close', () => {
    if (!res.writableEnded) closed.abort();
  });
  try {
    const upstream = await forward(upstreamPayload, closed.signal);
    const text = await upstream.text();
    res.writeHead(upstream.status, {
      'content-type': 'application/json; charset=utf-8',
      'cache-control': 'no-store',
    });
    res.end(text);
  } catch (error) {
    const aborted = error && (error.name === 'AbortError' || error.name === 'TimeoutError');
    send(res, aborted ? 504 : 502, { error: { message: aborted ? 'upstream_timeout' : 'upstream_unreachable' } });
  } finally {
    inFlight -= 1;
  }
});

server.listen(PORT, () => {
  console.log(`relay listening on :${PORT} -> ${upstreamUrl.host} ` +
    `(tokens=${TOKENS.size}, daily=${DAILY_REQUESTS}, per_ip_hourly=${PER_IP_HOURLY}, concurrency=${CONCURRENCY})`);
});
