#!/usr/bin/env python3
"""12306 扫码登录：生成二维码 → 轮询扫码 → 完成登录 → 回写共享 session 文件。

用法:
  python scripts/login_qr_flow.py [--wait 120]

输出（stdout，逐行）:
  QR_READY  <uuid>        二维码已生成到 .runtime/login_qr.png
  SCANNED                   检测到扫码
  LOGIN_OK  <username>    登录成功，cookie 已回写 .runtime/session.cookies
  QR_EXPIRED / NOT_SCANNED / FAIL:<msg>

退出码: 0=成功 2=二维码过期 3=超时未扫 4+=登录失败
"""
import argparse
import base64
import sys
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests

BASE_URL = "https://kyfw.12306.cn"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
QR_FILE = PROJECT_ROOT / ".runtime" / "login_qr.png"
SESSION_FILE = PROJECT_ROOT / ".runtime" / "session.cookies"


def main() -> int:
    parser = argparse.ArgumentParser(description="12306 扫码登录")
    parser.add_argument("--wait", type=float, default=120.0, help="等待扫码最长秒数（默认 120）")
    args = parser.parse_args()

    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": f"{BASE_URL}/otn/leftTicket/init?linktypeid=dc",
        "Origin": BASE_URL,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    })

    # 1. 创建二维码
    r = s.post(f"{BASE_URL}/passport/web/create-qr64", data={"appid": "otn"}, timeout=15)
    p = r.json()
    if str(p.get("result_code")) != "0":
        print(f"FAIL: 获取二维码失败 {p.get('result_message') or p}")
        return 4
    image = base64.b64decode(p["image"], validate=True)
    uuid = str(p["uuid"])
    QR_FILE.parent.mkdir(parents=True, exist_ok=True)
    QR_FILE.write_bytes(image)
    print(f"QR_READY {uuid}", flush=True)

    # 2. 轮询扫码状态（12306: 0=未扫 1=已扫 2=已确认 4=过期）
    deadline = time.time() + args.wait
    scanned = False
    while time.time() < deadline:
        r = s.post(f"{BASE_URL}/passport/web/checkqr", data={"uuid": uuid, "appid": "otn"}, timeout=15)
        p = r.json()
        code = str(p.get("result_code", ""))
        if code in ("1", "2"):
            scanned = True
            break
        if code == "4":
            print("QR_EXPIRED", flush=True)
            return 2
        time.sleep(1)
    if not scanned:
        print("NOT_SCANNED", flush=True)
        return 3
    print("SCANNED", flush=True)

    # 3. 完成登录
    r = s.post(f"{BASE_URL}/passport/web/auth/uamtk", data={"appid": "otn"}, timeout=15)
    p = r.json()
    if str(p.get("result_code")) != "0":
        print(f"FAIL: uamtk {p.get('result_message') or p}")
        return 5
    token = p.get("newapptk")
    if not token:
        print("FAIL: 未获取到 newapptk")
        return 5

    r = s.post(f"{BASE_URL}/otn/uamauthclient", data={"tk": token}, timeout=15)
    p = r.json()
    if str(p.get("result_code")) != "0":
        print(f"FAIL: uamauthclient {p.get('result_message') or p}")
        return 6
    username = str(p.get("username") or "Success")

    # 4. 验证会话
    r = s.post(f"{BASE_URL}/otn/login/checkUser", data={"_json_att": ""}, timeout=15)
    p = r.json()
    flag = (p.get("data") or {}).get("flag")
    if flag is not True:
        print(f"FAIL: checkUser flag={flag}")
        return 7

    # 5. 回写共享 session 文件（与 client.save_cookies 相同格式）
    jar = MozillaCookieJar(str(SESSION_FILE))
    for cookie in s.cookies:
        jar.set_cookie(cookie)
    jar.save(ignore_discard=True, ignore_expires=True)
    print(f"LOGIN_OK {username}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
