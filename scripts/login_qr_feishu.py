#!/usr/bin/env python3
"""12306 扫码登录（飞书发图版）：生成二维码 → 飞书 API 发图 → 轮询扫码 → 登录 → 回写 session。

输出:
  FEISHU_SENT   图片已通过飞书发送
  LOGIN_OK <username> / QR_EXPIRED / NOT_SCANNED / FAIL:<msg>
"""
import argparse
import base64
import json
import subprocess
import sys
import time
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests

BASE_URL = "https://kyfw.12306.cn"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
QR_FILE = PROJECT_ROOT / ".runtime" / "login_qr.png"
SESSION_FILE = PROJECT_ROOT / ".runtime" / "session.cookies"
RECEIVER_OPEN_ID = "ou_044c5e9259df3c7f3ee559f1c03b4ba6"
CONFIG_FILE = Path.home() / ".openclaw" / "openclaw.json"


def feishu_token() -> str:
    import os
    cfg = json.loads(CONFIG_FILE.read_text())
    feishu = (cfg.get("channels") or {}).get("feishu") or {}
    app_id = feishu.get("appId")
    app_secret = os.environ.get("FEISHU_APP_SECRET")
    if not app_id or not app_secret:
        raise RuntimeError("缺少飞书 appId 或 FEISHU_APP_SECRET 环境变量")
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    p = r.json()
    if p.get("code") != 0:
        raise RuntimeError(f"获取飞书 token 失败: {p}")
    return p["tenant_access_token"]


def start_keepalive(days: float = 7.0) -> None:
    """拉起保活守护（先杀旧实例）。失败不影响登录结果。"""
    import os
    import signal
    try:
        old = subprocess.run(["pgrep", "-f", "session_keepalive_loop.py"],
                             capture_output=True, text=True, timeout=10)
        for pid in old.stdout.split():
            try:
                os.kill(int(pid), signal.SIGTERM)
            except (ValueError, ProcessLookupError):
                pass
        time.sleep(0.5)
        log_file = PROJECT_ROOT / ".runtime" / "keepalive.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(log_file, "a") as log:
            subprocess.Popen(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "session_keepalive_loop.py"),
                 "--days", str(days)],
                stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, cwd=str(PROJECT_ROOT))
        print(f"KEEPALIVE_STARTED days={days}", flush=True)
    except Exception as e:
        print(f"KEEPALIVE_START_FAIL: {e}", flush=True)


def feishu_send_image(token: str, image_path: Path) -> None:
    with open(image_path, "rb") as f:
        r = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": "message"},
            files={"image": (image_path.name, f, "image/png")}, timeout=15)
    p = r.json()
    if p.get("code") != 0:
        raise RuntimeError(f"上传图片失败: {p}")
    image_key = p["data"]["image_key"]
    r = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"receive_id": RECEIVER_OPEN_ID, "msg_type": "image",
              "content": json.dumps({"image_key": image_key})}, timeout=15)
    p = r.json()
    if p.get("code") != 0:
        raise RuntimeError(f"发送图片失败: {p}")


def run(wait: float = 180.0) -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"),
        "Referer": f"{BASE_URL}/otn/leftTicket/init?linktypeid=dc",
        "Origin": BASE_URL,
    })

    # 0. 飞书 token（提前拿，二维码生成后尽快发图）
    try:
        token = feishu_token()
    except Exception as e:
        print(f"FAIL: {e}", flush=True)
        return 10

    # 1. 创建二维码
    r = s.post(f"{BASE_URL}/passport/web/create-qr64", data={"appid": "otn"}, timeout=15)
    p = r.json()
    if str(p.get("result_code")) != "0":
        print(f"FAIL: 获取二维码失败 {p.get('result_message') or p}", flush=True)
        return 4
    image = base64.b64decode(p["image"], validate=True)
    uuid = str(p["uuid"])
    QR_FILE.parent.mkdir(parents=True, exist_ok=True)
    QR_FILE.write_bytes(image)

    # 2. 立即发飞书
    try:
        feishu_send_image(token, QR_FILE)
        print("FEISHU_SENT", flush=True)
    except Exception as e:
        print(f"FAIL: 飞书发图失败 {e}", flush=True)
        return 11

    # 3. 轮询扫码（12306: 0=未扫 1=已扫未确认 2=已确认 3=过期）
    #    必须等到 2（手机端确认）才能 uamtk，1 时调会报“用户未登录”
    deadline = time.time() + wait
    while time.time() < deadline:
        r = s.post(f"{BASE_URL}/passport/web/checkqr", data={"uuid": uuid, "appid": "otn"}, timeout=15)
        p = r.json()
        code = str(p.get("result_code", ""))
        if code == "1":
            print("SCANNED 等待手机端确认...", flush=True)
        elif code == "2":
            print("CONFIRMED", flush=True)
            break
        elif code == "3":
            print("QR_EXPIRED", flush=True)
            return 2
        time.sleep(1)
    else:
        print("NOT_SCANNED", flush=True)
        return 3

    # 4. 完成登录
    r = s.post(f"{BASE_URL}/passport/web/auth/uamtk", data={"appid": "otn"}, timeout=15)
    p = r.json()
    if str(p.get("result_code")) != "0":
        print(f"FAIL: uamtk {p.get('result_message') or p}", flush=True)
        return 5
    tok = p.get("newapptk")
    if not tok:
        print("FAIL: 未获取到 newapptk", flush=True)
        return 5

    r = s.post(f"{BASE_URL}/otn/uamauthclient", data={"tk": tok}, timeout=15)
    p = r.json()
    if str(p.get("result_code")) != "0":
        print(f"FAIL: uamauthclient {p.get('result_message') or p}", flush=True)
        return 6
    username = str(p.get("username") or "Success")

    r = s.post(f"{BASE_URL}/otn/login/checkUser", data={"_json_att": ""}, timeout=15)
    p = r.json()
    if (p.get("data") or {}).get("flag") is not True:
        print("FAIL: checkUser flag=false", flush=True)
        return 7

    jar = MozillaCookieJar(str(SESSION_FILE))
    for cookie in s.cookies:
        jar.set_cookie(cookie)
    jar.save(ignore_discard=True, ignore_expires=True)
    print(f"LOGIN_OK {username}", flush=True)
    start_keepalive(7.0)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="12306 扫码登录（飞书发图版）")
    parser.add_argument("--wait", type=float, default=180.0, help="等待扫码最长秒数（默认 180）")
    args = parser.parse_args()
    return run(args.wait)


if __name__ == "__main__":
    sys.exit(main())
