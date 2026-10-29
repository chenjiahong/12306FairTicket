"""test_http_query.py — 纯 HTTP 免登录查 12306 余票（curl_cffi 模拟 Chrome 指纹）
流程：init 页拿匿名会话 cookie → queryG 查询
"""
import sys
from urllib.parse import quote
from curl_cffi import requests as creq

DATE = "2026-10-27"
FROM_NAME, FROM_CODE, TO_CODE = "珠海", "ZHQ", "CBQ"

impersonate = sys.argv[1] if len(sys.argv) > 1 else "chrome"
print(f"[impersonate] {impersonate}")

s = creq.Session(impersonate=impersonate)

# 1) 访问 init 页（建立匿名会话 + 拿 cookie）
TO_NAME = "潮汕"
init_url = (f"https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc"
            f"&fs={quote(FROM_NAME)},{FROM_CODE}&ts={quote(TO_NAME)},{TO_CODE}&date={DATE}&purpose_codes=ADULT")
r1 = s.get(init_url, timeout=30)
print(f"[init] status={r1.status_code} final={r1.url[:80]} len={len(r1.text)}")
print(f"[init] cookies: {dict(s.cookies)}")
if "error.html" in r1.url or r1.status_code != 200:
    print("[FAIL] init 页被拦")
    sys.exit(1)

# 2) queryG 查询
q_url = (f"https://kyfw.12306.cn/otn/leftTicket/queryG"
         f"?leftTicketDTO.train_date={DATE}&leftTicketDTO.from_station={FROM_CODE}"
         f"&leftTicketDTO.to_station={TO_CODE}&purpose_codes=ADULT")
headers = {
    "Accept": "*/*",
    "Cache-Control": "no-cache",
    "If-Modified-Since": "0",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": init_url,
}
r2 = s.get(q_url, headers=headers, timeout=30)
print(f"[queryG] status={r2.status_code} final={r2.url[:100]} len={len(r2.text)}")
try:
    j = r2.json()
    res = j.get("data", {}).get("result", [])
    print(f"[OK] JSON 有效, 车次数={len(res)}")
    for x in res:
        a = x.split("|")
        print(f"  {a[3]}  {a[8]}→{a[9]}  历时{a[10]}  一等座:{a[31]}  二等座:{a[30]}  无座:{a[26]}")
except Exception as e:
    print(f"[FAIL] 非 JSON: {e}")
    print("head:", r2.text[:200])
    sys.exit(1)
