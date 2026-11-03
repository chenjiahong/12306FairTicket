#!/usr/bin/env python3
"""12306 会话保活检查（供 cron 调用）：会话有效则静默退出；失效则自动飞书发二维码重登。

用法:
  python scripts/session_keepalive.py [--wait 300]

退出码:
  0 = 会话有效（或自动重登成功）
  2 = 会话失效且自动重登失败（二维码未扫/过期），需人工介入
  3 = 无法确认会话状态（网络异常等），未发二维码
"""
import argparse
import json
import sys
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


def check_session():
    """True=有效 False=失效 None=无法确认（网络异常/文件损坏）"""
    if not SESSION_FILE.exists():
        return False
    try:
        jar = MozillaCookieJar(str(SESSION_FILE))
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception:
        return False
    s = requests.Session()
    s.cookies = jar
    s.headers.update({"User-Agent": UA})
    try:
        r = s.post(f"{BASE_URL}/otn/login/checkUser", data={"_json_att": ""}, timeout=15)
        p = r.json()
        return (p.get("data") or {}).get("flag") is True
    except Exception:
        return None


def notify_feishu(text: str) -> None:
    """失败时发飞书文字通知（复用 login_qr_feishu 的凭证逻辑）。"""
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
            print(f"NOTIFY_FAIL: {p}", flush=True)
    except Exception as e:
        print(f"NOTIFY_FAIL: {e}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="12306 会话保活检查")
    parser.add_argument("--wait", type=float, default=300.0,
                        help="失效时等待扫码最长秒数（默认 300）")
    args = parser.parse_args()

    valid = check_session()
    if valid is True:
        print("SESSION_OK", flush=True)
        return 0
    if valid is None:
        print("CHECK_FAILED: 无法确认会话状态（网络异常?），未发二维码", flush=True)
        notify_feishu("12306 会话保活：无法确认会话状态（网络异常），本次未发二维码。"
                      "如抢票前请手动验证: cd ~/code/12306FairTicket && python3 scripts/session_keepalive.py")
        return 3

    print("SESSION_INVALID: 会话已失效，开始自动重登（飞书发二维码）...", flush=True)
    sys.path.insert(0, str(SCRIPTS_DIR))
    import login_qr_feishu as lqf
    rc = lqf.run(wait=args.wait)
    if rc == 0:
        print("RELOGIN_OK", flush=True)
        return 0
    print(f"RELOGIN_FAILED rc={rc}", flush=True)
    notify_feishu(f"12306 会话已失效，自动重登失败（退出码 {rc}，二维码可能未扫或已过期）。\n"
                  "请手动重登: cd ~/code/12306FairTicket && python3 scripts/login_qr_feishu.py")
    return 2


if __name__ == "__main__":
    sys.exit(main())
