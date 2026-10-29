#!/usr/bin/env python3
"""从正在运行的 Chrome profile 提取 cookie（CDP 旁路实例法，绕开 Chrome 磁盘加密）。

原理（2026-10-19 / 10-21 验证）：
1. 复制 Chrome profile 到临时目录（运行中的 Chrome 锁着原 profile，副本不受影响）
2. 删除副本里的 Singleton* 锁
3. 用副本启动 headless Chrome，带 --remote-debugging-port 和 --remote-allow-origins=*
   （Chrome 148 不加 allow-origins 会 403 拒绝 WebSocket）
4. CDP Network.getAllCookies 直接读明文 cookie
5. 按 Netscape 格式写入技能会话文件 .runtime/session.cookies
   注意：domain 不以点开头时 domain-flag 必须写 FALSE，否则 http.cookiejar 加载报错

仅标准库，无第三方依赖。用法（仓库根目录）：
    .venv/bin/python scripts/extract_browser_cookies.py [--domain 12306] [--port 9226]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE = Path.home() / ".config" / "google-chrome"
CHROME_BIN = "/opt/google/chrome/chrome"
IGNORE_DIRS = {"Cache", "Code Cache", "GPUCache", "ShaderCache", "DawnGraphiteCache",
               "DawnWebGPUCache", "Service Worker", "optimization_guide_model_store"}


def copy_profile(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for item in src.iterdir():
        if item.name in IGNORE_DIRS or item.name.startswith("Singleton"):
            continue
        if item.is_dir():
            shutil.copytree(item, dst / item.name,
                            ignore=shutil.ignore_patterns(*IGNORE_DIRS), symlinks=True)
        else:
            shutil.copy2(item, dst / item.name)
    # 保险：清掉可能残留的锁
    for lock in dst.glob("Singleton*"):
        lock.unlink()


def wait_cdp(port: int, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=3) as r:
                return json.load(r)
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"CDP 端口 {port} 未就绪")


class MiniWS:
    """最小 WebSocket 客户端（CDP 够用，仅标准库）。"""

    def __init__(self, url: str):
        assert url.startswith("ws://"), url
        hostport, path = url[5:].split("/", 1)
        self.sock = socket.create_connection(("127.0.0.1", int(hostport.split(":")[1] if ":" in hostport else 80)), timeout=30)
        key = base64.b64encode(os.urandom(16)).decode()
        req = (f"GET /{path} HTTP/1.1\r\nHost: {hostport}\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("WebSocket 握手失败（连接被断开）")
            resp += chunk
        if b"101" not in resp.split(b"\r\n", 1)[0]:
            raise RuntimeError(f"WebSocket 握手被拒: {resp[:200]!r}")

    def send_json(self, obj: dict) -> None:
        data = json.dumps(obj).encode()
        header = bytearray([0x81])  # FIN + text
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        header += mask
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        self.sock.sendall(bytes(header) + masked)

    def recv_json(self) -> dict:
        def readn(k: int) -> bytes:
            buf = b""
            while len(buf) < k:
                chunk = self.sock.recv(k - len(buf))
                if not chunk:
                    raise RuntimeError("WebSocket 连接关闭")
                buf += chunk
            return buf
        b0, b1 = readn(2)
        ln = b1 & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", readn(2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", readn(8))[0]
        return json.loads(readn(ln))

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


def cdp_get_cookies(port: int) -> list:
    tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10))
    page = next(t for t in tabs if t.get("type") == "page")
    ws = MiniWS(page["webSocketDebuggerUrl"])
    ws.send_json({"id": 1, "method": "Network.getAllCookies"})
    while True:
        msg = ws.recv_json()
        if msg.get("id") == 1:
            ws.close()
            return msg.get("result", {}).get("cookies", [])
    ws.close()


def write_netscape_jar(path: Path, cookies: list) -> int:
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        domain = c.get("domain", "")
        if not domain:
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        lines.append("\t".join([domain, flag, c.get("path", "/"), "TRUE", "0",
                                c.get("name", ""), c.get("value", "")]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    return len(lines) - 1


def main() -> int:
    ap = argparse.ArgumentParser(description="从 Chrome profile 提取 cookie 写入技能会话文件")
    ap.add_argument("--profile", default=str(DEFAULT_PROFILE), help="Chrome profile 目录")
    ap.add_argument("--domain", default="12306", help="域名过滤关键字")
    ap.add_argument("--port", type=int, default=9226, help="CDP 调试端口")
    ap.add_argument("--out", default=str(REPO_ROOT / ".runtime" / "session.cookies"),
                    help="输出 Netscape cookie 文件")
    args = ap.parse_args()

    profile = Path(args.profile)
    if not (profile / "Default" / "Cookies").exists():
        print(f"错误: {profile}/Default/Cookies 不存在", file=sys.stderr)
        return 1

    workdir = Path(f"/tmp/chrome-cdp-copy-{args.port}")
    print(f"复制 profile -> {workdir}")
    copy_profile(profile, workdir)

    chrome = subprocess.Popen(
        [CHROME_BIN, "--headless=new", "--disable-gpu",
         f"--remote-debugging-port={args.port}", "--remote-allow-origins=*",
         "--no-first-run", "--no-sandbox", "--disable-dev-shm-usage",
         f"--user-data-dir={workdir}", "--noerrdialogs", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_cdp(args.port)
        print("CDP 就绪，读取 cookie")
        all_cookies = cdp_get_cookies(args.port)
        picked = [c for c in all_cookies if args.domain in c.get("domain", "")]
        if not picked:
            print(f"错误: 未找到域名含 {args.domain} 的 cookie", file=sys.stderr)
            return 1
        n = write_netscape_jar(Path(args.out), picked)
        print(f"已写入 {n} 条 cookie -> {args.out}")
        for c in picked:
            print(f"  {c['domain']} {c['name']} = {c['value'][:16]}...")
        return 0
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=10)
        except Exception:
            chrome.kill()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
