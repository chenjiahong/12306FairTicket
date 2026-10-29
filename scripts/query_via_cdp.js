#!/usr/bin/env node
/**
 * query_via_cdp.js — 12306 余票查询（CDP 通道，无需登录/扫码）
 *
 * 原理：
 *   12306 的 WAF 会拦截"裸请求"，也会拦截页面自己的自动查询（实测重试 10 次全被挡）。
 *   但"真浏览器 + 站点原样请求头"的手动 fetch 能通。本脚本：
 *     1. CDP 连一个真 Chrome（自动拉起或复用，持久 profile 保持匿名会话）
 *     2. 导航到 leftTicket/init 页（带 fs/ts/date 参数，建立 Referer + 会话）
 *     3. 在页面里用站点原样的请求头 fetch queryG 接口 → 拿 JSON
 *     4. 按 12306 页面源码的字段映射解析，输出表格
 *   全程匿名会话，无需扫码登录。
 *
 * 用法：
 *   node query_via_cdp.js <日期YYYY-MM-DD> <出发站> <到达站> [选项]
 *   例: node query_via_cdp.js 2026-10-27 珠海 潮汕
 *
 * 选项：
 *   --port <n>      CDP 端口（默认 9334）
 *   --json          输出原始 queryG JSON
 *   --no-launch     不自动拉起 Chrome（连不上就报错退出）
 *
 * 依赖：puppeteer-core（NODE_PATH=/home/chenjiahong/node_modules）
 * Chrome：C:\Program Files (x86)\Google\Chrome\Application\chrome.exe
 */
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

// ---------- 参数解析 ----------
function parseArgs(argv) {
  const args = { port: 9334, json: false, noLaunch: false };
  const pos = [];
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--port') args.port = parseInt(argv[++i], 10);
    else if (a === '--json') args.json = true;
    else if (a === '--no-launch') args.noLaunch = true;
    else pos.push(a);
  }
  if (pos.length < 3) {
    console.error('用法: node query_via_cdp.js <日期YYYY-MM-DD> <出发站> <到达站> [--port N] [--json] [--no-launch]');
    process.exit(2);
  }
  const [date, from, to] = pos;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) { console.error('日期格式应为 YYYY-MM-DD'); process.exit(2); }
  args.date = date; args.from = from; args.to = to;
  return args;
}

// ---------- 站点名 → 电报码 ----------
function loadStations() {
  const p = path.join(__dirname, '..', '.runtime', 'stations.json');
  const d = JSON.parse(fs.readFileSync(p, 'utf8'));
  return d.stations || d;
}
function resolveStation(name, stations) {
  if (stations[name]) return stations[name];
  if (/^[A-Z]{1,3}$/.test(name)) return name; // 电报码直传
  const hits = Object.keys(stations).filter(k => k === name || k.includes(name) || name.includes(k));
  if (hits.length === 1) return stations[hits[0]];
  if (hits.length > 1) {
    const exact = hits.find(k => k === name);
    if (exact) return stations[exact];
    throw new Error(`"${name}" 匹配到多个车站: ${hits.slice(0, 10).join('、')}${hits.length > 10 ? '…' : ''}，请用完整站名`);
  }
  throw new Error(`未找到车站 "${name}"`);
}

// ---------- Chrome 拉起 ----------
const CHROME = 'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe';
const PROFILE = 'C:\\Users\\KSO\\AppData\\Local\\Temp\\chrome-12306-query';
function launchChrome(port) {
  console.error(`[launch] 拉起 Chrome (CDP 端口 ${port})...`);
  const args = [`--remote-debugging-port=${port}`, '--remote-allow-origins=*',
    `--user-data-dir=${PROFILE}`, '--no-first-run', '--no-default-browser-check', 'about:blank'];
  execSync(`powershell -Command "Start-Process -FilePath '${CHROME}' -ArgumentList '${args.join(' ')}'"`, { timeout: 15000 });
}
async function waitCdp(port, ms) {
  const t0 = Date.now();
  while (Date.now() - t0 < ms) {
    try { const r = await fetch(`http://127.0.0.1:${port}/json/version`); if (r.ok) return true; } catch (e) {}
    await new Promise(r => setTimeout(r, 500));
  }
  return false;
}

// ---------- 字段映射（来自 12306 页面 queryLeftTicket_end_js.js 的 cN 函数）----------
const F = ['secretStr','buttonText','train_no','station_train_code','startTele','endTele','fromTele','toTele',
  'start_time','arrive_time','lishi','canWebBuy','yp_info','start_train_date','train_seat_feature','location_code',
  'from_station_no','to_station_no','is_support_card','controlled_train_flag',
  'gg','gr','qt','rw','rz','tz','wz','yb','yw','yz','ze','zy','swz','srrb'];
// 席别字段名 → 中文（拼音首字母 + 页面列位置双重验证）
const SEAT = { swz:'商务座', gg:'特等座', zy:'一等座', ze:'二等座', tz:'二等包座',
  gr:'高级软卧', rw:'软卧', yw:'硬卧', rz:'软座', yz:'硬座', wz:'无座', qt:'其他' };
const SEAT_ORDER = ['swz','gg','zy','ze','tz','gr','rw','yw','rz','yz','wz','qt'];

// ---------- 主流程 ----------
(async () => {
  const args = parseArgs(process.argv);
  const stations = loadStations();
  const fromCode = resolveStation(args.from, stations);
  const toCode = resolveStation(args.to, stations);
  console.error(`[resolve] ${args.from}=${fromCode}  ${args.to}=${toCode}  日期=${args.date}`);

  const puppeteer = require('puppeteer-core');
  let browser;
  try {
    browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${args.port}`, defaultViewport: null });
  } catch (e) {
    if (args.noLaunch) { console.error(`[error] CDP 端口 ${args.port} 连不上（--no-launch，不自动拉起）`); process.exit(1); }
    launchChrome(args.port);
    if (!(await waitCdp(args.port, 20000))) { console.error('[error] Chrome 拉起后 CDP 端口仍未就绪'); process.exit(1); }
    browser = await puppeteer.connect({ browserURL: `http://127.0.0.1:${args.port}`, defaultViewport: null });
  }

  const page = (await browser.pages())[0] || await browser.newPage();
  const initUrl = `https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc&fs=${encodeURIComponent(args.from)},${fromCode}&ts=${encodeURIComponent(args.to)},${toCode}&date=${args.date}&purpose_codes=ADULT`;
  console.error(`[nav] ${initUrl}`);
  await page.goto(initUrl, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // 用站点原样的请求头 fetch queryG（页面自动查询被 WAF 挡，手动 fetch 可通）
  const ref = page.url();
  const q = await page.evaluate(async (ref, date, fc, tc) => {
    const url = 'https://kyfw.12306.cn/otn/leftTicket/queryG'
      + `?leftTicketDTO.train_date=${date}&leftTicketDTO.from_station=${fc}&leftTicketDTO.to_station=${tc}&purpose_codes=ADULT`;
    try {
      const r = await fetch(url, {
        credentials: 'include',
        headers: { 'Accept':'*/*', 'Cache-Control':'no-cache', 'If-Modified-Since':'0',
          'X-Requested-With':'XMLHttpRequest', 'Referer': ref },
      });
      const text = await r.text();
      let parsed = null; try { parsed = JSON.parse(text); } catch (e) {}
      return { status: r.status, ok: r.ok, finalUrl: r.url, isJson: !!parsed, parsed };
    } catch (e) { return { error: String(e) }; }
  }, ref, args.date, fromCode, toCode);

  if (args.json) { console.log(JSON.stringify(q, null, 2)); await browser.disconnect(); process.exit(0); }

  if (!q.isJson || !q.parsed || !q.parsed.data || !Array.isArray(q.parsed.data.result)) {
    console.error('[error] queryG 未返回有效 JSON（可能被 WAF 拦截）。status=' + q.status + ' finalUrl=' + (q.finalUrl||''));
    console.error('[hint] 换出口 IP / 稍后重试，或人工打开 CDP 窗口查看');
    await browser.disconnect();
    process.exit(1);
  }

  const trains = q.parsed.data.result.map(s => {
    const a = s.split('|');
    const o = {}; F.forEach((k, i) => o[k] = a[i]);
    const seats = {};
    for (const k of SEAT_ORDER) { const v = o[k]; if (v && v !== '--') seats[SEAT[k]] = v; }
    return { train: o.station_train_code, dep: o.start_time, arr: o.arrive_time, dur: o.lishi, seats };
  });

  console.log(`\n${args.date} ${args.from} → ${args.to}  共 ${trains.length} 车次\n`);
  if (trains.length === 0) { console.log('没有查询到符合条件的车次。'); await browser.disconnect(); process.exit(0); }
  console.log('| 车次 | 区间 | 历时 | 余票（席别:数量） |');
  console.log('|---|---|---|---|');
  for (const t of trains) {
    const seatStr = Object.entries(t.seats).map(([k, v]) => `${k}:${v}`).join('  ');
    console.log(`| ${t.train} | ${t.dep}→${t.arr} | ${t.dur} | ${seatStr || '—'} |`);
  }
  await browser.disconnect();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
