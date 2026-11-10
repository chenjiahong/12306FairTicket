---
name: ticket-grab
description: 12306 抢票与查余票：用户说抢票/买票/高铁票/火车票/查余票/查票/还有票吗时。查余票走纯 HTTP 免登录通道（curl_cffi 模拟 Chrome 指纹，CDP 真浏览器兜底）；抢票生成配置、启动 12306FairTicket、二维码发飞书、结果通知。
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
   - `kind=qr_ready` 或 `qr_status` 且 `data.status=waiting`：用 `MEDIA:` 行发送 `.runtime/login_qr.png`，提示"用 12306 APP 扫码登录"；`data.uuid` 变化说明二维码刷新，重新发图。**注意：登录二维码有效期只有 20 秒，看到 `qr_ready` 必须立刻发图，不能先做别的（查配置、发消息等），慢了用户扫了也白扫，只能等刷新重发。**
   - `kind=candidate`：播报票源（车次/席别/余票）。
   - `kind=error`：播报错误内容，等用户指示。
   - `kind=finished`：停止轮询，按 `data.exit_code` 汇报：0=下单成功（提醒 30 分钟内去 12306 付款）；1=失败或到停止时间；130=用户中断。
7. 用户要停止：`process kill` 对应 exec 会话，确认进程退出后汇报。

## 浏览器登录态提取（免扫码）

用户在 WSL Chrome（`~/.config/google-chrome`）登录 12306 后，可直接提取 cookie 写入会话文件，免扫码：

```
.venv/bin/python scripts/extract_browser_cookies.py [--domain 12306] [--port 9226]
```

原理：Chrome 磁盘 cookie 是加密的，直接解密不可靠。脚本复制 profile 到 /tmp 临时目录（跳过 Cache 类大目录和 Singleton 锁），用副本启动 headless Chrome 带 `--remote-debugging-port` + `--remote-allow-origins=*`（Chrome 148 缺此参数 WebSocket 握手 403），CDP `Network.getAllCookies` 读明文 cookie，按 Netscape 格式写 `.runtime/session.cookies`。仅标准库，无第三方依赖。

注意：
- Netscape 格式 domain 不以点开头时 domain-flag 必须写 `FALSE`，否则 `http.cookiejar` 加载报错
- 提取后先 `check_session` 验证；flag=false 说明浏览器里的会话本身已失效（常见死因：12306 APP 登录同账号互踢），需用户重新登录后再提取
- 浏览器会话寿命短（小时级），关键操作前都先验证

## 扫码登录脚本（会话过期时用）

会话失效（`checkUser` flag=false）时重新登录，不用走 GUI 流程：

```
.venv/bin/python scripts/login_qr_feishu.py [--wait 180]   # 二维码经飞书 API 直接发到用户对话
.venv/bin/python scripts/login_qr_flow.py [--wait 120]     # 二维码存本地 .runtime/login_qr.png，手动发
```

流程：create-qr64 → 发图 → 轮询 checkqr → 登录 → 回写 `.runtime/session.cookies` → **自动拉起 7 天保活守护**。

关键点：
- checkqr 状态 `0`=未扫 `1`=已扫未确认 `2`=已确认 `3`=过期。**必须等到 2 才能调 uamtk**，状态 1 时调会报"用户未登录"（用户扫完要在 APP 里点"确认登录"）
- 二维码 20 秒过期；生成后必须立刻发图
- 飞书凭证：appId 从 `~/.openclaw/openclaw.json` 的 `channels.feishu.appId` 读，appSecret 从环境变量 `FEISHU_APP_SECRET` 读（配置里是占位符，直接读配置会 `app secret invalid`）

**保活机制（默认无 cron，绑定登录动作）**：
- 登录成功后自动拉起 `scripts/session_keepalive_loop.py`（先杀旧实例）：每 45s checkUser + 回写轮换 cookie，保活 7 天；会话被踢（flag=false）时飞书文字通知。日志 `.runtime/keepalive.log`。手动跑：`setsid nohup .venv/bin/python scripts/session_keepalive_loop.py --days 7 >> .runtime/keepalive.log 2>&1 &`
- **坑（2026-11-05 实测）：从 OpenClaw exec 会话手动拉起保活必须用 `setsid nohup ... &`**。只写 `nohup ... &` 的话，保活是那个 exec 会话的子进程，OpenClaw 回收 exec 会话时会杀掉整个进程组，保活被静默杀掉（无 traceback、无退出日志、进程直接消失），会话随后裸放被 12306 空闲清理。`setsid` 让进程脱离 exec 会话的进程组，exec 会话回收不影响它。
- `scripts/session_keepalive.py`：手动检查工具。会话有效→静默退出；失效→自动飞书发码重登；需要时再跑，不挂 cron
- 注意：保活守护与 GUI/CLI 共享 session 文件，文件 mtime 变化时守护会自动同步 cookie，避免旧 JSESSIONID 误判失效

## 查余票（免登录免扫码）

用户只想查某日期某区间还有没有票、不用抢票时，用这条通道。无需登录、无需扫码，全程匿名会话。

**首选：纯 HTTP（`scripts/query_http.py`，无需浏览器，秒级）**

```
.venv/bin/python scripts/query_http.py <日期YYYY-MM-DD> <出发站> <到达站> [--json] [--impersonate X]
```

例：`.venv/bin/python scripts/query_http.py 2026-10-27 珠海 潮汕` → 输出车次/区间/历时/各席别余票表格。站名支持中文（自动解析电报码，`.runtime/stations.json`）或电报码直传。

原理（重要）：12306 WAF 按 **TLS/HTTP2 指纹**（JA3 + h2 SETTINGS）拦截，不卡身份、不弹 JS 挑战（真浏览器匿名查询全程无验证码）。Python `requests` 裸请求必死（指纹不对）；`curl_cffi impersonate="chrome"` 复刻 Chrome 指纹后两步走通：
1. `GET leftTicket/init?linktypeid=dc&fs=名,码&ts=名,码&date=..&purpose_codes=ADULT` → 拿匿名会话 cookie（JSESSIONID + BIGipServer*）
2. `GET leftTicket/queryG?leftTicketDTO.train_date=..&from_station=..&to_station=..&purpose_codes=ADULT`，同会话 + 站点原样头：`X-Requested-With: XMLHttpRequest`、`If-Modified-Since: 0`、`Cache-Control: no-cache`、`Accept: */*`、`Referer`=init URL（中文需百分号编码）

`queryG` 返回 `data.result` 是管道符分隔字符串数组；字段下标→名称映射来自页面 `queryLeftTicket_end_js.js` 的 `cN` 函数（脚本内已固化）。席别字段：`swz`=商务座 `gg`=特等座 `zy`=一等座 `ze`=二等座 `tz`=二等包座 `gr`=高级软卧 `rw`=软卧 `yw`=硬卧 `rz`=软座 `yz`=硬座 `wz`=无座 `qt`=其他（拼音首字母+页面列位置双重验证）。`--`/空=该席别无票。

被 WAF 拦时（init/queryG 非 JSON 或 error.html）：先 `--impersonate chrome124`（或 chrome120/safari 等）换指纹，或稍后重试；仍不行退回下面 CDP 版。

**指纹失效风险（重要，长期维护必读）**

指纹本身**不会随时间过期**——`impersonate` 是静态的 TLS/HTTP2 握手参数复刻，不是 cookie/token，无时效。失效只发生在 **12306 那边改规则**时，共三种触发方式：

| 失效方式 | 说明 | 应对 |
|---|---|---|
| WAF 升级检测 | 从粗粒度 JA3 升级到更细的 JA4 / h2 帧序，静态复刻细节对不上 | 升级 curl_cffi（`.venv/bin/pip install -U curl_cffi`，默认 `chrome` 指纹会跟到新版 Chrome） |
| 特定版本被点名 | WAF 规则专门屏蔽当前模拟的 Chrome 版本 | `--impersonate` 换版本（chrome124/chrome131/chrome146/chrome150/safari180/firefox147…，40+ 可选） |
| 加 JS 行为挑战（最致命） | 12306 在匿名查询加 JS 验证码/JS 算 token，纯 HTTP 不执行 JS，指纹再对也没用 | **退回 CDP 真浏览器版**（能执行 JS、指纹永远真实最新） |

实际风险评估：
- 最可能的失效不是"指纹过期"而是"加 JS 挑战"；但 `queryG` 匿名接口历史上一直是纯指纹拦截（实测全程无验证码），给高频匿名查票加挑战伤正常用户，12306 短期大概率不加，风险低。
- 指纹版本基本不用担心：Chrome TLS 握手参数跨版本变化小，WAF 更可能拦"不像 Chrome 的"而非"某个具体版本"；curl_cffi 升级后默认指纹自动变新。
- **双保险结论**：纯 HTTP 为主 + CDP 真浏览器兜底，最坏情况（加 JS 挑战）也有退路，不会因指纹问题全灭。被拦时按上表手动切换，不做自动降级（保持行为可预期）。

**兜底：CDP 真浏览器（`scripts/query_via_cdp.js`）**

```
NODE_PATH=/home/chenjiahong/node_modules node scripts/query_via_cdp.js <日期> <出发站> <到达站> [--port 9334] [--json]
```

若未来 WAF 加 JS 挑战导致纯 HTTP 失效，用这版：CDP 连真 Chrome（连不上自动拉起，持久 profile `C:\Users\KSO\AppData\Local\Temp\chrome-12306-query`，端口默认 9334）→ 导航 init 页 → 页面内用站点原样头 `fetch` queryG。依赖 `puppeteer-core`；Chrome 在 `C:\Program Files (x86)\Google\Chrome\Application\chrome.exe`。注意：页面自己的自动查询会被 WAF 挡（重试 10 次全失败），必须手动 fetch。

## 参考

- **多段行程（中转）一次只能买一张**：12306 规定有未支付订单时不能下新单，所以多段必须"下单→付款→再下下一段"，不能一次下多段。下单顺序**路程长、难买的优先**（长距离车次余票少、先下先占；短途容易买，放后面即使前一段付款慢也来得及）。
- 事件 JSONL 每行 `{time, kind, message, data}`；`qr_ready` 的 `data.image_bytes.b64` 是二维码 PNG 的 base64（备用，优先直接发 `login_qr.png` 文件）。
- 跨站扩展：启动阶段对指定车次拉官方经停站（queryByTrainNo），自动生成买长（终点后移）备选，事件 `cross_station_expanded` 逐条播报新增站对，转发给用户；买短（起点前移）需 `allow_short=True`，越站乘车需补票、高铁有被要求下车风险，用户明确要求才开。
- 登录会话存 `.runtime/session.cookies`，有效期内重启免扫码（事件 `session_checked.state=active`）。脚本登录成功后自动保活 7 天（见"扫码登录脚本"节）。
- **会话空闲寿命很短（实测 ≤ 约 9 小时，2026-11-03 23:05 最后活跃 → 11-04 08:16 失效，期间零请求、已排除互踢），12306 会清理空闲会话，所以登录后必须靠保活守护续命，不能裸放过夜**；同账号别处登录（12306 APP 等）也会立即互踢。
- 相对路径均相对仓库根目录；`.runtime/` 已被 gitignore。
- 开售时间不准会白跑：不确定时让用户查 12306 公告，或 `--start-at` 留空立即开始查票。
- 席别白名单：商务座/特等座/一等座/二等座/高级软卧/软卧/硬卧/一等卧/二等卧/软座/硬座/无座。
- 本项目无许可证声明，仅限个人自用，不公开分发。
