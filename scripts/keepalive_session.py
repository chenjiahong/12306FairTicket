#!/usr/bin/env python3
"""12306 登录会话保活：定期 checkUser 并回写轮换后的 cookie。

用法:
  python scripts/keepalive_session.py --config /path/config.py [--interval 45] [--max-minutes 0]

退出码:
  0 = 达到 --max-minutes 时长正常退出（--max-minutes 0 时不主动退出）
  2 = 登录会话已失效，需要重新扫码
  3 = 连续检查失败，无法确认会话状态
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ticket_app.configuration import load_config
from ticket_app.client import RailwayClient


def main() -> int:
    parser = argparse.ArgumentParser(description="12306 登录会话保活")
    parser.add_argument("--config", required=True, help="抢票配置文件路径")
    parser.add_argument("--interval", type=float, default=45.0, help="检查间隔秒数（默认 45）")
    parser.add_argument("--max-minutes", type=float, default=0.0, help="最长保活分钟数，0 表示不限")
    parser.add_argument("--fail-limit", type=int, default=3, help="连续检查失败上限（默认 3）")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(Path(args.config))
    client = RailwayClient(cfg)

    start = time.time()
    failures = 0
    round_no = 0
    while True:
        round_no += 1
        if args.max_minutes > 0 and (time.time() - start) >= args.max_minutes * 60:
            logging.info("保活达到 %.1f 分钟，正常退出", args.max_minutes)
            return 0
        try:
            valid = client.check_session()
        except Exception as exc:  # noqa: BLE001 - 网络/解析失败需计数而非崩溃
            failures += 1
            logging.warning("第 %d 次检查失败（%s），%d/%d", round_no, type(exc).__name__, failures, args.fail_limit)
            if failures >= args.fail_limit:
                logging.error("连续 %d 次检查失败，无法确认会话状态，退出", failures)
                return 3
        else:
            failures = 0
            client.maybe_save_cookies()
            if not valid:
                logging.error("第 %d 次检查: 登录会话已失效，需要重新扫码", round_no)
                return 2
            logging.info("第 %d 次检查: 会话有效（已运行 %.0f 秒）", round_no, time.time() - start)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
