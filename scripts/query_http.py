#!/usr/bin/env python3
"""
query_http.py — 12306 余票查询（纯 HTTP，免登录免扫码，无需浏览器）

原理：
  12306 WAF 按 TLS/HTTP2 指纹（JA3 + h2 SETTINGS）拦截，不卡身份、不弹 JS 挑战。
  curl_cffi impersonate="chrome" 复刻 Chrome 指纹后纯 HTTP 即可走通：
    1. GET leftTicket/init（带 fs/ts/date）→ 拿匿名会话 cookie
    2. GET leftTicket/queryG（同会话 + 站点原样请求头）→ JSON
  字段下标→名称映射来自 12306 页面 queryLeftTicket_end_js.js 的 cN 函数。
  若未来 WAF 加 JS 挑战导致本脚本失效，退回 CDP 版：scripts/query_via_cdp.js

用法：
  .venv/bin/python scripts/query_http.py <日期YYYY-MM-DD> <出发站> <到达站> [选项]
  例: .venv/bin/python scripts/query_http.py 2026-10-27 珠海 潮汕

选项：
  --json            输出原始 queryG JSON
  --impersonate X   指纹 profile（默认 chrome；被拦时可试 chrome124/chrome120 等）
"""
import argparse
import json
import sys
from pathlib import Path
from urllib.parse import quote

from curl_cffi import requests as creq

REPO = Path(__file__).resolve().parent.parent
STATIONS_FILE = REPO / ".runtime" / "stations.json"

# ---------- 字段映射（12306 页面 cN 函数）----------
F = ['secretStr','buttonText','train_no','station_train_code','startTele','endTele','fromTele','toTele',
     'start_time','arrive_time','lishi','canWebBuy','yp_info','start_train_date','train_seat_feature','location_code',
     'from_station_no','to_station_no','is_support_card','controlled_train_flag',
     'gg','gr','qt','rw','rz','tz','wz','yb','yw','yz','ze','zy','swz','srrb']
SEAT = {'swz':'商务座','gg':'特等座','zy':'一等座','ze':'二等座','tz':'二等包座',
        'gr':'高级软卧','rw':'软卧','yw':'硬卧','rz':'软座','yz':'硬座','wz':'无座','qt':'其他'}
SEAT_ORDER = ['swz','gg','zy','ze','tz','gr','rw','yw','rz','yz','wz','qt']


def load_stations():
    d = json.loads(STATIONS_FILE.read_text(encoding='utf-8'))
    return d.get('stations', d)


def resolve_station(name, stations):
    if name in stations:
        return stations[name]
    if name.isascii() and name.isupper() and 1 <= len(name) <= 3:
        return name  # 电报码直传
    hits = [k for k in stations if k == name or name in k or k in name]
    if len(hits) == 1:
        return stations[hits[0]]
    if hits:
        exact = next((k for k in hits if k == name), None)
        if exact:
            return stations[exact]
        raise SystemExit(f'错误: "{name}" 匹配到多个车站: {"、".join(hits[:10])}{"…" if len(hits) > 10 else ""}，请用完整站名')
    raise SystemExit(f'错误: 未找到车站 "{name}"')


def main():
    ap = argparse.ArgumentParser(description='12306 余票查询（纯 HTTP 免登录）')
    ap.add_argument('date', help='YYYY-MM-DD')
    ap.add_argument('from_station', help='出发站（中文名或电报码）')
    ap.add_argument('to_station', help='到达站（中文名或电报码）')
    ap.add_argument('--json', action='store_true', help='输出原始 queryG JSON')
    ap.add_argument('--impersonate', default='chrome', help='curl_cffi 指纹 profile（默认 chrome）')
    args = ap.parse_args()

    stations = load_stations()
    fc = resolve_station(args.from_station, stations)
    tc = resolve_station(args.to_station, stations)
    print(f'[resolve] {args.from_station}={fc}  {args.to_station}={tc}  日期={args.date}', file=sys.stderr)

    s = creq.Session(impersonate=args.impersonate)

    # 1) init 页：建立匿名会话
    init_url = ('https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc'
                f'&fs={quote(args.from_station)},{fc}&ts={quote(args.to_station)},{tc}'
                f'&date={args.date}&purpose_codes=ADULT')
    r1 = s.get(init_url, timeout=30)
    if r1.status_code != 200 or 'error.html' in r1.url:
        print(f'[error] init 页被拦: status={r1.status_code} final={r1.url}', file=sys.stderr)
        print('[hint] 换 --impersonate 或稍后重试；仍不行退回 CDP 版 query_via_cdp.js', file=sys.stderr)
        sys.exit(1)

    # 2) queryG 查询
    q_url = ('https://kyfw.12306.cn/otn/leftTicket/queryG'
             f'?leftTicketDTO.train_date={args.date}&leftTicketDTO.from_station={fc}'
             f'&leftTicketDTO.to_station={tc}&purpose_codes=ADULT')
    headers = {'Accept': '*/*', 'Cache-Control': 'no-cache', 'If-Modified-Since': '0',
               'X-Requested-With': 'XMLHttpRequest', 'Referer': init_url}
    r2 = s.get(q_url, headers=headers, timeout=30)

    try:
        j = r2.json()
        result = j['data']['result']
    except Exception:
        print(f'[error] queryG 未返回有效 JSON: status={r2.status_code} final={r2.url}', file=sys.stderr)
        print('head:', r2.text[:200], file=sys.stderr)
        print('[hint] 可能被 WAF 拦截：换 --impersonate / 稍后重试 / 退回 CDP 版', file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(j, ensure_ascii=False, indent=2))
        return

    trains = []
    for x in result:
        a = x.split('|')
        o = dict(zip(F, a))
        seats = {SEAT[k]: o[k] for k in SEAT_ORDER if o.get(k) and o[k] != '--'}
        trains.append({'train': o['station_train_code'], 'dep': o['start_time'],
                       'arr': o['arrive_time'], 'dur': o['lishi'], 'seats': seats})

    print(f'\n{args.date} {args.from_station} → {args.to_station}  共 {len(trains)} 车次\n')
    if not trains:
        print('没有查询到符合条件的车次。')
        return
    print('| 车次 | 区间 | 历时 | 余票（席别:数量） |')
    print('|---|---|---|---|')
    for t in trains:
        seat_str = '  '.join(f'{k}:{v}' for k, v in t['seats'].items()) or '—'
        print(f"| {t['train']} | {t['dep']}→{t['arr']} | {t['dur']} | {seat_str} |")


if __name__ == '__main__':
    main()
