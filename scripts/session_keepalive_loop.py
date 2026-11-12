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


def get_uptime() -> float:
    """Return system uptime in seconds (detects WSL restarts)."""
    try:
        with open("/proc/uptime") as f:
            return float(f.read().split()[0])
    except Exception:
        return -1.0


def cookie_names(s: requests.Session) -> list:
    return sorted(c.name for c in s.cookies)


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
    start_time = time.time()
    prev_uptime = get_uptime()
    jsid_changes = 0
    last_jsid = next((c.value for c in s.cookies if c.name == "JSESSIONID"), None)
    check_count = 0
    last_success = time.time()
    log(f"KEEPALIVE_START days={args.days} interval={args.interval} (jitter 0.7x~1.3x) "
        f"uptime={prev_uptime:.0f}s JSESSIONID={last_jsid[:20] if last_jsid else 'N/A'}...")

    while time.time() < end:
        time.sleep(random.uniform(args.interval * 0.7, args.interval * 1.3))
        # 检测 WSL 重启
        cur_uptime = get_uptime()
        if prev_uptime >= 0 and cur_uptime >= 0 and cur_uptime < prev_uptime:
            log(f"WSL_RESTART_DETECTED uptime {prev_uptime:.0f}s -> {cur_uptime:.0f}s")
        prev_uptime = cur_uptime
        sync_from_file(s, state)
        try:
            r = s.post(f"{BASE_URL}/otn/login/checkUser", data={"_json_att": ""}, timeout=15)
            p = r.json()
            flag = (p.get("data") or {}).get("flag")
            check_count += 1
            # 记录每次 checkUser 的详细信息
            jsid = next((c.value for c in s.cookies if c.name == "JSESSIONID"), None)
            if jsid and jsid != last_jsid:
                jsid_changes += 1
                log(f"JSESSIONID_CHANGED #{jsid_changes} {last_jsid[:16] if last_jsid else 'N/A'} -> {jsid[:16]}")
                last_jsid = jsid
            alive_min = (time.time() - start_time) / 60
            log(f"CHECK_OK #{check_count} flag=True http={r.status_code} "
                f"alive={alive_min:.1f}m jsid={jsid[:16] if jsid else 'N/A'} "
                f"cookies={cookie_names(s)}")
            last_success = time.time()
        except Exception as e:
            log(f"CHECK_ERROR #{check_count + 1} {type(e).__name__}: {e}")
            continue
        if flag is not True:
            alive_min = (time.time() - start_time) / 60
            last_ok_min = (time.time() - last_success) / 60
            log(f"SESSION_DEAD alive={alive_min:.1f}m last_check_ok={last_ok_min:.1f}m ago "
                f"checks={check_count} jsid_changes={jsid_changes} "
                f"last_jsid={last_jsid[:20] if last_jsid else 'N/A'} "
                f"resp={json.dumps(p, ensure_ascii=False)[:300]}")
            notify_feishu(f"12306 会话保活：会话已失效（存活 {alive_min:.0f} 分钟），请重新扫码: "
                          f"cd ~/code/12306FairTicket && python3 scripts/login_qr_feishu.py")
            return 2
        snap = snapshot(s)
        if snap != last_snap:
            save_session(s)
            last_snap = snap
            log("COOKIES_SAVED")

    log("KEEPALIVE_DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
