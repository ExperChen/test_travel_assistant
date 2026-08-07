# Better Travel Assistant

输入**时间 + 目的地**（或直接说一句话），产出一份可执行的行程：往返机票（含到达机场）、
目的地酒店、热门景点召回与打分、逐日路径规划与时间表，最后由模型写成一段说明。

```
"国庆假期从北京去成都玩4天，预算600一晚，想看大熊猫"
        ↓ POST /api/v1/trips/parse
出发地 北京 · 目的地 成都 · 出发 2026-10-01（原话「国庆假期」）
返程 2026-10-04（按「玩 4 天」推算）· 预算 ¥600 · 必去 大熊猫
```

后端服务 + API 契约，无前端。目的地限中国大陆。

- 编排：LangGraph（航班 / 酒店 / 景点三条分支并行，关键节点 `interrupt()` 问用户）
- 接口：FastAPI + SSE（创建即返回，进度走事件流）
- 数据：SerpAPI（Google Flights / Hotels）+ 高德（POI / 路径规划）
- 模型：只用来把排好的行程翻译成文字；**分天、排序、算时间全是确定性算法**

设计文档见 [docs/architecture/system-architecture.md](docs/architecture/system-architecture.md)。

---

## 1. 装环境

需要 Python 3.11+（开发用的是 3.14）。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

## 2. 配密钥

```powershell
Copy-Item .env.example .env           # macOS/Linux: cp .env.example .env
```

然后编辑 `.env`：

| 变量 | 去哪儿申请 | 免费额度 | 不填会怎样 |
| --- | --- | --- | --- |
| `SERPAPI_KEY` | <https://serpapi.com/manage-api-key> | 250 次/月 | 查不了机票和酒店 |
| `AMAP_KEY` | <https://console.amap.com/dev/key/app>（**必须选「Web 服务 API」**） | 5000 次/日 | 查不了景点和路线 |
| `LLM_API_KEY` | DeepSeek / 阿里百炼 / 智谱 / Kimi 任选 | 各家不同 | 行程说明退化为模板，其余照常 |

> **`.env` 已在 `.gitignore` 里，不要提交。** 高德 Key 类型选错（JS/Android/iOS）会一直报 `10001`。
>
> 换模型供应商只改 `LLM_BASE_URL` + `LLM_MODEL` + `LLM_API_KEY` 三行，代码一行不动。
> 完全不想接模型就设 `LLM_ENABLED=false`——说明文案改由确定性模板生成，里面每个数字
> 都直接取自行程数据，内容和模型路径一致。

## 3. 先跑 demo（推荐从这里开始）

不启服务，直接在终端看完整输出。

```bash
# ⓪ 一句话说需求。--prompt 不带值 = 启动后再敲，省掉 shell 引号转义
python scripts/demo_trip.py --prompt --parse-only     # 只看解析结果，零额度
python scripts/demo_trip.py --prompt --dry-run        # 解析完接着排行程，仍是零额度
python scripts/demo_trip.py --prompt                  # 真实规划，约 5 次 SerpAPI

# ① 零额度：全假数据，先看输出长什么样
python scripts/demo_trip.py 杭州 --dry-run

# ② 真实数据：约 5 次 SerpAPI + 约 20 次高德
python scripts/demo_trip.py 成都 --days 5

# ③ 手动回答"选哪个机场 / 哪家酒店"
python scripts/demo_trip.py 成都 --interactive

# ④ 只跑景点召回链路——只花高德额度，不碰 SerpAPI
python scripts/demo_attractions.py 成都 --verbose

# ⑤ 航班对不上时：原始响应 vs 我们解析的结果，并排看
python scripts/debug_flights.py PEK CTU --return-leg
```

常用参数：`--from PEK`（出发地，填 IATA 三字码可省一次额度）、`--days`、`--start-in`、
`--budget 800`（每晚上限）、`--pace relaxed|standard|packed`、
`--transport transit|driving|walking`、`--must 西湖`、`--avoid 宋城`、`--verbose`。

> 相同参数 1 小时内命中缓存，不再扣额度，反复调同一条行程是安全的。

## 4. 起服务

```bash
uvicorn app.main:app --reload --port 8000 --workers 1
```

- 交互式接口文档：<http://127.0.0.1:8000/docs>
- 探活：`curl http://127.0.0.1:8000/health` —— 只报告"密钥配没配、缓存多大"，
  **不发任何上游请求**（那会白烧 SerpAPI 额度）

> ⚠️ **必须 `--workers 1`。** checkpointer（MemorySaver）、SSE 事件缓冲、配额记账、
> 超时清扫任务全在进程内，多 worker 下 `/answer` 可能落到没有该 thread 的进程上。
> 上多副本前要先把这几样换成共享存储（架构文档 §4.4 / §8.2）。

### 走一遍完整调用

```bash
# 0) 可选：一句话 → TripRequest 草稿。不创建行程，不烧 SerpAPI 额度
curl -s -X POST localhost:8000/api/v1/trips/parse \
  -H 'Content-Type: application/json' -d @prompt.json
# → {"request": {...}, "fields": [{"key":"outbound_date","value":"2026-10-01（周四）",
#                                  "origin":"prompt","note":"原话「国庆假期」"}, ...],
#    "missing": [], "questions": [], "degraded": false}
# 确认无误后，把 request 原样提交给下一步即可

# 1) 创建 —— 立刻返回 202，规划在后台跑
curl -s -X POST localhost:8000/api/v1/trips \
  -H 'Content-Type: application/json' \
  -d '{"departure_city":"PEK","destination_city":"成都",
       "outbound_date":"2026-09-05","return_date":"2026-09-09",
       "auto_select":true}'
# → {"trip_id":"trp_xxx","status":"running","stream_url":"/api/v1/trips/trp_xxx/stream"}

# 2) 订阅进度（stage / partial / warning / question / done / error）
curl -N localhost:8000/api/v1/trips/trp_xxx/stream

# 3) 中途收到 question 事件时回答（auto_select=false 才会问）
curl -X POST localhost:8000/api/v1/trips/trp_xxx/answer \
  -H 'Content-Type: application/json' \
  -d '{"question_id":"flight.itinerary","value":"1"}'

# 4) 任何时候取当前快照
curl localhost:8000/api/v1/trips/trp_xxx
```

> Windows 的 shell 会按本地编码（GBK）传参，`-d '{"destination_city":"成都"}'` 里的中文
> 到服务端就成了乱码，返回 `There was an error parsing the body`。把 JSON 存成 UTF-8 文件、
> 用 `-d @body.json` 就好了；或者直接在 `/docs` 里点。

断线重连带上 `Last-Event-ID` 头即可补发漏掉的事件。
问题超过 `INTERRUPT_TIMEOUT_S`（默认 600 秒）没人回答，后台清扫任务会按默认值自动放行——
用户关掉页面不会让行程永久卡在 `waiting_input`。

设了 `APP_API_KEY` 的话，每个请求都要带 `-H 'X-API-Key: ...'`；留空则关闭鉴权（仅限本地开发）。

限流：创建 10 次/分钟，读取 60 次/分钟。创建限得严是因为**每次创建烧 5 次 SerpAPI**，
按读接口的速率放行，一分钟就能把整月免费额度打掉 1.2 倍。

## 5. 测试与检查

```bash
pytest                            # 517 个用例，全程 mock，不碰真实 API，约 11 秒
ruff check app scripts
```

分层跑：

| 目录 | 个数 | 管什么 | 耗时 |
| --- | --- | --- | --- |
| `app/tests/unit` | 340 | 纯函数：日期、坐标、打分、排期、需求解析 | 2.4s |
| `app/tests/contract` | 57 | 上游响应 → 内部模型的解析，用 `docs/` 里摘的真实响应快照 | 0.6s |
| `app/tests/e2e` | 82 | 全 mock provider 跑通整张图：并行、中断恢复、超时、兜底链 | 5.7s |
| `app/tests/api` | 38 | HTTP 契约：SSE、`/answer`、鉴权、限流、超时清扫 | 2.2s |

```bash
pytest app/tests/unit                          # 只跑某一层
pytest app/tests/unit/test_prompt_parser.py    # 只跑某个文件
pytest -k "holiday or ambiguous" -v            # 按名字挑
pytest -x --lf                                 # 只重跑上次失败的，第一个错就停
```

**测试绝不会碰真实 API。** [conftest.py](app/tests/conftest.py) 里三个 autouse fixture 兜着底：
`_isolated_clients` 把全局客户端换成假 key 的实例（否则节点会去 new 一个读真实 `.env` 的），
`_no_real_llm` 关掉模型（要验模型行为的用例显式注入 `FakeLLM`），`_clean_cache` 防止用例
之间通过全局缓存互相污染。真实 API 的验证走 demo 脚本，不放进测试。

`live` marker 已在 [pyproject.toml](pyproject.toml) 注册，目前还没有用例使用它。

## 6. 排查

| 现象 | 原因 / 处理 |
| --- | --- |
| 日志看不懂 | `LOG_JSON=false LOG_LEVEL=DEBUG`，或 demo 脚本加 `--verbose` |
| 航班号/价格看着不对 | `python scripts/debug_flights.py PEK CTU --raw`，原始响应和解析结果并排比。**同一航线的数据随时间变**，实测一小时内从 ¥4685/`3U 8890` 变成 ¥1927/`TV 9956`——要对比就在同一次运行里比 |
| 航班结果很怪（中转比直飞贵） | 试 `--gl cn` 对照。销售地会换掉整个结果集，依据见[接口文档 §4.1](docs/flight-agent/serpapi-google-flights-api.md) |
| PowerShell 里输出是乱码 | 先 `chcp 65001`。脚本本身已强制 UTF-8 输出，乱的是 PowerShell 按 GBK 重新解码管道 |
| 高德返回 `10001` | Key 类型不对，必须是「Web 服务 API」 |
| 每次规划都卡 30 秒 | 模型服务连不通。设 `LLM_ENABLED=false` 跳过 |
| Gemini 报 `FAILED_PRECONDITION: User location is not supported` | Google 按服务端 IP 归属地拒绝，中国大陆直连不通。用 `LLM_PROVIDER=openai_compatible`（默认），或挂支持地区的代理 |
| `CITY_NOT_FOUND` / `DESTINATION_UNSUPPORTED` | 目的地限中国大陆；港澳台及省级行政区（如"广东"）不受支持，要填具体城市 |
| SerpAPI 额度告急 | `/health` 看缓存命中率；demo 优先用 `--dry-run`，只调景点链路用 `demo_attractions.py` |
