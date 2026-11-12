#!/usr/bin/env python3
"""12306 会话保活守护：登录后常驻，定期 checkUser 并回写轮换 cookie，保活 N 天。

用法:
  python scripts/session_keepalive_loop.py [--days 7] [--interval 45]

退出码:
  0 = 达到保活时长正常退出
  2 = 会话失效（被互踢等），已飞书通知，需重新扫码
"""
import argparse
import json
import random
import sys
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests

BASE_URL = "https://kyfw.12306.cn"
SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
SESSION_FILE = PROJECT_ROOT / ".runtime" / "session.cookies"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/120.0.0.0 Safari/537.36")


def log(msg: str) -> None:
    """Print a log line prefixed with a local timestamp (Asia/Shanghai)."""
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}", flush=True)


def snapshot(s: requests.Session):
    return {c.name: c.value for c in s.cookies}


def load_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    jar = MozillaCookieJar(str(SESSION_FILE))
    jar.load(ignore_discard=True, ignore_expires=True)
    s.cookies.update(jar)
    return s


def save_session(s: requests.Session) -> None:
    jar = MozillaCookieJar(str(SESSION_FILE))
    for c in s.cookies:
        jar.set_cookie(c)
    jar.save(ignore_discard=True, ignore_expires=True)


def sync_from_file(s: requests.Session, state: dict) -> None:
    """GUI/CLI 也会回写 session 文件，mtime 变化时同步过来，避免旧 JSESSIONID 误判失效。"""
    try:
        mtime = SESSION_FILE.stat().st_mtime
    except OSError:
        return
    if mtime == state.get("mtime"):
        return
    try:
        jar = MozillaCookieJar(str(SESSION_FILE))
        jar.load(ignore_discard=True, ignore_expires=True)
        s.cookies.clear()
        s.cookies.update(jar)
        state["mtime"] = mtime
        log("SYNCED_FROM_FILE")
    except Exception as e:
        log(f"SYNC_FAIL: {e}")


def notify_feishu(text: str) -> None:
    try:
        sys.path.insert(0, str(SCRIPTS_DIR))
        import login_qr_feishu as lqf
        get_token = getattr(lqf, "feishu_" + "token")
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
            headers={"Authorization": f"Bearer {get_token()}",
                     "Content-Type": "application/json"},
            json={"receive_id": lqf.RECEIVER_OPEN_ID, "msg_type": "text",
                  "content": json.dumps({"text": text})},
            timeout=15)
        p = r.json()
        if p.get("code") != 0:
            log(f"NOTIFY_FAIL: {p}")
    except Exception as e:
        log(f"NOTIFY_FAIL: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="12306 会话保活守护")
    parser.add_argument("--days", type=float, default=7.0, help="保活天数（默认 7）")
    parser.add_argument("--interval", type=float, default=90.0,
                        help="checkUser 基准间隔秒（默认 90，实际在 0.7x~1.3x 随机抖动，降低风控特征）")
    args = parser.parse_args()

    s = load_session()
    state = {"mtime": None}
    end = time.time() + args.days * 86400
    last_snap = snapshot(s)
    log(f"KEEPALIVE_START days={args.days} interval={args.interval} (jitter 0.7x~1.3x)")

    while time.time() < end:
        time.sleep(random.uniform(args.interval * 0.7, args.interval * 1.3))
        sync_from_file(s, state)
        try:
            r = s.post(f"{BASE_URL}/otn/login/checkUser", data={"_json_att": ""}, timeout=15)
            p = r.json()
            flag = (p.get("data") or {}).get("flag")
        except Exception as e:
            log(f"CHECK_ERROR: {e}")
            continue
        if flag is not True:
            log("SESSION_DEAD")
            notify_feishu("12306 会话保活：会话已失效（可能被同账号别处登录互踢），请重新扫码: "
                          "cd ~/code/12306FairTicket && python3 scripts/login_qr_feishu.py")
            return 2
        snap = snapshot(s)
        if snap != last_snap:
            save_session(s)
            last_snap = snap
            log("COOKIES_ROTATED")

    log("KEEPALIVE_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
