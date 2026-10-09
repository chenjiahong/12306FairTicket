---
name: ticket-grab
description: 12306 抢票：用户说抢票/买票/高铁票/火车票时。生成配置、启动 12306FairTicket、二维码发飞书、结果通知。
---

# 12306 抢票（12306FairTicket）

仓库：`~/code/12306FairTicket`（分支 openclaw-integration，venv `.venv`）。同一 12306 账号同时只跑一个实例；不代付，抢到后用户手动付款。

## 步骤

1. 收集行程：日期、出发站、到达站、车次（可空=不限）、席别、乘车人（须与 12306 常用乘车人姓名一致）、开售时间。缺什么问什么，一次问全，得到完整行程。
2. 在仓库根目录生成配置：
   `.venv/bin/python scripts/gen_config.py --config-out <slug>.py --date YYYY-MM-DD --passengers "姓名" --item "FROM:TO:TRAIN:SEAT" [--start-at "HH:MM:SS"] [--stop-at "HH:MM:SS"] [--cross-station]`
   多个备选加多个 `--item`；不限车次时 TRAIN 段留空（`北京:上海::硬卧`）。输出"配置已生成"即成功。
   用户说"没票就买长/跨站买"时加 `--cross-station`（默认只买长，最多后移 5 站；`--max-extend N` 调站数 0-5，`--allow-short` 才允许买短）。
3. 校验：`.venv/bin/python main.py --config <slug>.py --validate-config`，输出"配置校验通过"且 exit 0 才算过；失败时把报错原样汇报，等用户指示。
4. 向用户复述配置要点（日期/区间/车次/席别/乘车人/时间窗/是否自动下单），等用户明确确认后才启动。
5. 后台启动：exec（background）`.venv/bin/python main.py --config <slug>.py --events-file .runtime/<slug>_events.jsonl`，记下 exec 会话 id。
6. 轮询事件文件（每 15-30 秒，`read` 增量读 JSONL，按行处理）：
   - `kind=qr_ready` 或 `qr_status` 且 `data.status=waiting`：用 `MEDIA:` 行发送 `.runtime/login_qr.png`，提示"用 12306 APP 扫码登录"；`data.uuid` 变化说明二维码刷新，重新发图。
   - `kind=candidate`：播报票源（车次/席别/余票）。
   - `kind=error`：播报错误内容，等用户指示。
   - `kind=finished`：停止轮询，按 `data.exit_code` 汇报：0=下单成功（提醒 30 分钟内去 12306 付款）；1=失败或到停止时间；130=用户中断。
7. 用户要停止：`process kill` 对应 exec 会话，确认进程退出后汇报。

## 参考

- 事件 JSONL 每行 `{time, kind, message, data}`；`qr_ready` 的 `data.image_bytes.b64` 是二维码 PNG 的 base64（备用，优先直接发 `login_qr.png` 文件）。
- 跨站扩展：启动阶段对指定车次拉官方经停站（queryByTrainNo），自动生成买长（终点后移）备选，事件 `cross_station_expanded` 逐条播报新增站对，转发给用户；买短（起点前移）需 `allow_short=True`，越站乘车需补票、高铁有被要求下车风险，用户明确要求才开。
- 登录会话存 `.runtime/session.cookies`，有效期内重启免扫码（事件 `session_checked.state=active`）。
- 相对路径均相对仓库根目录；`.runtime/` 已被 gitignore。
- 开售时间不准会白跑：不确定时让用户查 12306 公告，或 `--start-at` 留空立即开始查票。
- 席别白名单：商务座/特等座/一等座/二等座/高级软卧/软卧/硬卧/一等卧/二等卧/软座/硬座/无座。
- 本项目无许可证声明，仅限个人自用，不公开分发。
