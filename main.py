import argparse
import base64
import json
import logging
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional

from ticket_app.configuration import AppError, DEFAULT_CONFIG_FILE, load_config
from ticket_app.logging_utils import RedactingFormatter
from ticket_app.runner import TicketRunner
from ticket_app.runtime import RuntimeEvent


class JsonlEventSink:
    """Append every runtime event as one JSON line for external monitoring.

    Each CLI run starts a fresh stream so stale events from a previous run
    never leak into the current one. Consumers (e.g. OpenClaw) can tail the
    file and react to kinds like ``qr_status``, ``candidate`` and ``finished``.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("w", encoding="utf-8")

    @staticmethod
    def _sanitize(value: Any) -> Any:
        """Make event data JSON-friendly: bytes become {"b64": ...} markers."""
        if isinstance(value, bytes):
            return {"b64": base64.b64encode(value).decode("ascii")}
        if isinstance(value, dict):
            return {key: JsonlEventSink._sanitize(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [JsonlEventSink._sanitize(item) for item in value]
        return value

    def emit(self, event: RuntimeEvent) -> None:
        record = {
            "time": datetime.fromtimestamp(event.timestamp).strftime("%Y-%m-%d %H:%M:%S"),
            "kind": event.kind,
            "message": event.message,
            "data": self._sanitize(event.data),
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        with self._lock:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self) -> None:
        with self._lock:
            self._fh.close()


def configure_logging(level: str, sensitive_terms: Iterable[str] = ()) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
            sensitive_terms=sensitive_terms,
        )
    )
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        handlers=[handler],
        force=True,
    )


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="12306 命令行抢票助手")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_FILE), help="配置脚本路径，默认使用同目录 config.py")
    parser.add_argument("--validate-config", action="store_true", help="只校验配置，不登录、不访问 12306")
    parser.add_argument(
        "--events-file",
        default=None,
        help="JSONL 事件流文件路径（默认 <配置目录>/.runtime/events.jsonl；传空字符串禁用）",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    config_path = Path(args.config).resolve()
    sensitive_terms: Iterable[str] = ()
    sink: Optional[JsonlEventSink] = None
    try:
        cfg = load_config(config_path)
        sensitive_terms = cfg.passenger_names
        configure_logging(cfg.log_level, sensitive_terms)
        logging.info("已加载配置: %s", cfg.config_path)
        if args.validate_config:
            logging.info("配置校验通过")
            return 0
        if args.events_file != "":
            events_path = Path(args.events_file) if args.events_file else config_path.parent / ".runtime" / "events.jsonl"
            if not events_path.is_absolute():
                events_path = config_path.parent / events_path
            sink = JsonlEventSink(events_path)
            logging.info("事件流写入: %s", events_path)
        return TicketRunner(cfg, event_sink=sink).run()
    except AppError as exc:
        configure_logging("INFO", sensitive_terms)
        logging.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        logging.warning("用户中断任务")
        return 130
    except Exception as exc:
        logging.exception("程序异常退出: %s", exc)
        return 1
    finally:
        if sink is not None:
            sink.close()


if __name__ == "__main__":
    raise SystemExit(main())
