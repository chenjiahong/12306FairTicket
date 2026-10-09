#!/usr/bin/env python3
"""Generate a 12306FairTicket config.py from command-line arguments.

Example:
  python scripts/gen_config.py \
    --config-out config.py \
    --date 2026-10-01 \
    --passengers "张三" \
    --item "北京南:上海虹桥:G101:二等座" \
    --item "北京:上海::硬卧" \
    --start-at "15:00:00" --stop-at "15:10:00"

--item format: FROM:TO:TRAIN:SEAT (TRAIN empty means all trains, use ":")
Validate after generating:  python main.py --config <out> --validate-config
"""
import argparse
import sys
from pathlib import Path

SEAT_TYPES = [
    "商务座", "特等座", "一等座", "二等座", "高级软卧", "软卧",
    "硬卧", "一等卧", "二等卧", "软座", "硬座", "无座",
]

TEMPLATE = '''# 12306 抢票配置（由 scripts/gen_config.py 生成，可手工微调）
# CLI: python main.py --config {config_name}

CART_ITEMS = {cart_items}

TRAIN_DATE = {train_date!r}
PASSENGER_NAMES = {passenger_names}
PASSENGER_TICKET_TYPES = {passenger_ticket_types}

START_AT = {start_at!r}
STOP_AT = {stop_at!r}

QUERY_INTERVAL_SECONDS = 0.6
MAX_RETRIES = 1000
PRE_QUERY_SECONDS = 1.5
HOT_QUERY_INTERVAL_SECONDS = 0.25
HOT_WINDOW_SECONDS = 5.0

AUTO_SUBMIT = {auto_submit}
CROSS_STATION = {cross_station}

SEAT_POSITION_PREFERENCES = []
BERTH_PREFERENCE = {{"lower": 0, "middle": 0, "upper": 0}}
QUIET_CARRIAGE_PREFERENCE = False

PURPOSE_CODES = "ADULT"
REQUEST_TIMEOUT_SECONDS = 10
LOGIN_QR_TIMEOUT_SECONDS = 180
LOGIN_QR_POLL_SECONDS = 1.0
TIME_SYNC_SAMPLES = 7
TIME_SYNC_MAX_RTT_SECONDS = 1.0
ORDER_WAIT_ATTEMPTS = 300
ORDER_WAIT_INTERVAL_SECONDS = 2.0
STATION_CACHE_DAYS = 7
LOG_LEVEL = "INFO"
PERF_LOG = True

QR_CODE_FILE = ".runtime/login_qr.png"
SESSION_FILE = ".runtime/session.cookies"
STATION_CACHE_FILE = ".runtime/stations.json"
PERSIST_SESSION = True
'''


def parse_item(text: str) -> dict:
    parts = [p.strip() for p in text.split(":")]
    if len(parts) != 4:
        raise SystemExit(f"--item 需要 FROM:TO:TRAIN:SEAT 四段（TRAIN 可留空），收到: {text!r}")
    frm, to, train, seat = parts
    if not frm or not to:
        raise SystemExit(f"--item 站名不能为空: {text!r}")
    if seat not in SEAT_TYPES:
        raise SystemExit(f"--item 席别无效: {seat!r}，支持: {'、'.join(SEAT_TYPES)}")
    return {
        "from_station": frm,
        "to_station": to,
        "train_scope": "specific" if train else "all",
        "train_code": train,
        "seat_type": seat,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 12306FairTicket 配置文件")
    parser.add_argument("--config-out", default="config.py", help="输出配置路径（默认 config.py）")
    parser.add_argument("--date", required=True, help="乘车日期 YYYY-MM-DD")
    parser.add_argument("--passengers", default="", help="乘车人，逗号分隔，需与 12306 常用乘车人一致")
    parser.add_argument("--item", action="append", required=True,
                        help="备选项 FROM:TO:TRAIN:SEAT，可重复（TRAIN 留空 = 不限车次）")
    parser.add_argument("--start-at", default="", help="开售时间 HH:MM:SS 或 YYYY-MM-DD HH:MM:SS，留空立即开始")
    parser.add_argument("--stop-at", default="", help="停止时间，留空不自动停止")
    parser.add_argument("--ticket-type", action="append", default=[],
                        help="乘车人票种 姓名:adult|student，可重复；默认用 12306 联系人类型")
    parser.add_argument("--no-auto-submit", action="store_true", help="只查票不自动下单")
    parser.add_argument("--cross-station", action="store_true",
                        help="启用跨站扩展：指定车次的直达没票时自动加买长备选（终点后移）")
    parser.add_argument("--max-extend", type=int, default=5, help="跨站最多延伸站数 0-5（默认 5）")
    parser.add_argument("--allow-short", action="store_true",
                        help="同时允许买短（起点前移）；越站乘车需补票，高铁慎用")
    args = parser.parse_args()
    if args.allow_short:
        args.cross_station = True
    if not 0 <= args.max_extend <= 5:
        raise SystemExit("--max-extend 必须是 0-5")
    cross_station = {"enable": args.cross_station, "max_extend": args.max_extend,
                     "allow_short": args.allow_short}

    cart = [parse_item(t) for t in args.item]
    passengers = [p.strip() for p in args.passengers.replace("，", ",").split(",") if p.strip()]
    if len(set(passengers)) != len(passengers):
        raise SystemExit("乘车人不能重复")
    ticket_types = {}
    for spec in args.ticket_type:
        name, kind = [s.strip() for s in spec.split(":", 1)]
        if kind not in ("adult", "student"):
            raise SystemExit(f"--ticket-type 票种必须是 adult 或 student: {spec!r}")
        if name not in passengers:
            raise SystemExit(f"--ticket-type 乘车人 {name!r} 不在 --passengers 中")
        ticket_types[name] = kind

    cart_repr = ",\n".join(
        '    {"from_station": %s, "to_station": %s,\n     "train_scope": %s, "train_code": %s, "seat_type": %s}'
        % (repr(i["from_station"]), repr(i["to_station"]), repr(i["train_scope"]),
           repr(i["train_code"]), repr(i["seat_type"]))
        for i in cart
    )
    content = TEMPLATE.format(
        config_name=Path(args.config_out).name,
        cart_items=f"[\n{cart_repr},\n]",
        train_date=args.date,
        passenger_names=repr(passengers),
        passenger_ticket_types=repr(ticket_types),
        start_at=args.start_at,
        stop_at=args.stop_at,
        auto_submit="False" if args.no_auto_submit else "True",
        cross_station=repr(cross_station),
    )
    out = Path(args.config_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"配置已生成: {out.resolve()}")
    print(f"备选 {len(cart)} 项，乘车人 {len(passengers)} 人，日期 {args.date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
