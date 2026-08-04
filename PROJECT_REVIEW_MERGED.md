# 合并问题清单与整改方案

> 本文档合并 `PROJECT_REVIEW.md`（静态代码审查，P0–P3 分级，定位到具体行号）与
> `PROJECT_IMPROVEMENT_REQUIREMENTS.md`（目标契约与实施阶段规划），去除重复内容，
> 并对照**当前代码实际状态**逐条复核结论（而非照抄旧报告）。
>
> 复核方式：`git status` 显示 `hotel_agent.py`、`main_agent.py` 等文件存在未提交改动，
> 说明部分问题可能已经被修复。本文档对每一条都重新读取当前代码验证，标注
> **已修复 / 部分修复 / 未修复**，并只对"未修复"和"部分修复"给出需要处理的方案。
>
> 本次仅做审查整理，未修改任何源代码。

---

## 0. 两份文档的关系

| 文档 | 定位 |
| --- | --- |
| `PROJECT_REVIEW.md` | "现状有什么问题" —— 代码级审查，P0–P3 分级，附行号和修复建议 |
| `PROJECT_IMPROVEMENT_REQUIREMENTS.md` | "目标应该是什么样" —— 统一数据契约、分阶段实施计划、验收标准 |

两者对同一批问题描述角度不同但指向一致（如 hotel_agent 重复、景点无时间字段、
出发城市硬编码、view_result 解析冗余、汇率表不一致等）。下面按"是否已解决"重新分类。

---

## 1. 已修复（两份文档均可视为已落实，无需再处理）

| # | 问题 | 对应旧文档编号 | 验证依据 |
| --- | --- | --- | --- |
| 1 | `hotel_agent.py` 整份重复实现 | REVIEW P2-1 / REQUIREMENTS 2.1 | 现文件仅 67 行、单一实现，且改用 `from app.tools.hotel_tool import search_hotels` 包内绝对导入，无 `sys.path` 注入 |
| 2 | 景点缺少 `arrival_time` / `departure_time`，行程退化为无排期列表 | REVIEW P0-1 / REQUIREMENTS 2.2 | `main_agent.py` 新增 `_schedule_attractions()` + `_attraction_timezone()`，按 `check_in`/`check_out` 天数与每日上限（`_MAX_ATTRACTIONS_PER_DAY=4`）生成带时区 ISO8601 时间戳；`test_main_agent_regressions.py` 已有断言覆盖 |
| 3 | `_build_standard_payload` 中 4 个条件相同、彼此不可达的 `elif` 分支 + 裸 `except:` | REVIEW P2-2 / REQUIREMENTS 2.4（部分） | 已重写为单一 `_extract_attraction_items()`，逻辑线性、无裸 except |
| 4 | LLM 降级路径把"第一个提到的城市"误判为目的地，导致出发/目的地互换 | REVIEW P1-1（方向性部分） | 新增 `_extract_departure_city()`，用 `从X到Y` / `from X to Y` 方向性正则；`_apply_user_route_constraints()` 让 LLM 主路径和降级路径都遵循用户显式给出的出发城市；有回归测试覆盖 |
| 5 | 出发城市默认值使用无提示 | REQUIREMENTS 2.3 的"须标注默认值"要求 | 现在仅在用户文本确实未提及出发城市时才使用 `Shenzhen` 兜底，并写入 `warnings` 数组 |

---

## 2. 新发现：一个后续决策点（两份旧文档都未提及，因为写作时后端还没修）

**后端现在已经能输出 `arrival_time`/`departure_time` 了，但前端从未跟进消费。**

`grep` 结果显示，`timelineItems` / `timelineByDate`（Pinia getter，定义于
`frontend/src/stores/itinerary.js`，实现于 `frontend/src/lib/transformItinerary.js`）
目前只被 store 自身引用，**没有任何 `.vue` 组件消费它们**——`TimelinePanel.vue` 早已改成平铺卡片。

也就是说 REVIEW 报告 P2-6 提出的"时间线代码二选一（补齐时间戳后复活 / 确定不做就删除）"
现在必须拍板：数据侧已经具备条件，只差前端接线。**这是当前投入产出比最高的一步**，
建议列为下一阶段的第一项。

另外，REQUIREMENTS 3.2 提出的完整 `days[]` 统一契约（把 flights / hotel_checkin /
hotel_checkout / attraction / transport 都归一成同一个 `items` 数组，带
`data_source`、`request_id`、候选航班酒店最多 3 条等）**尚未实现**——已落地的只是
REVIEW P0-1 建议的最小方案（仅给 `views[]` 补时间戳）。是否升级到完整 `days[]` 契约，
需要产品侧决策，见第 5 节。

---

## 3. 部分修复（遗留的次要问题）

### 3.1 目的地城市解析仍是两套独立逻辑
`parse_natural_language_to_hotel_json` 判定目的地用 `cities[-1] if cities else "Seoul"`
（取文本中**最后**提到的城市），`_extract_departure_city` 判定出发地用方向正则或
"前两个城市中的第一个"。两者各自独立、没有共享的"方向解析"函数，虽然能覆盖常见的
"从 A 到 B" 表达，但逻辑没有收敛成一个。非阻塞，建议后续合并为单一解析函数。

### 3.2 LLM 主路径与降级路径默认日期不一致
`main_agent.py:159`（LLM 提示词）缺日期时默认 `2026-03-26`/`2026-03-28`，而
`parse_natural_language_to_hotel_json`（降级路径）缺日期时默认 `2026-05-01`/`2026-05-04`。
两条路径行为不一致，且缺省发生时不写入 `warnings`（不满足 REQUIREMENTS 3.1 "默认值须
显式标注"的要求）。

**建议**：统一为一个模块级常量（如 `_DEFAULT_CHECK_IN` / `_DEFAULT_CHECK_OUT`），两条
路径共用；使用默认日期或默认城市（`Seoul`）时都追加到 `warnings`。

### 3.3 `run_test_main_agent_flow` 中一整块计算但从未使用的变量
`main_agent.py:587-609`：`attraction_names`、`attraction_durations`、`hotel_name`
被完整计算（注释称"用于路线规划"）但之后从未被读取；`transport_result` 恒为
`None`；`run_travel_agent` 被 import 但全项目未调用。

**建议**：这是"交通路线规划"功能做一半的残迹。要么把 `transportation_agent` 真正接进
`run_test_main_agent_flow`，要么整块删除（需要时可从 git 历史恢复）——保留只会误导后来者
以为该功能已实现。

---

## 4. 未修复问题（合并去重后的主清单，按严重程度）

### P0（建议优先处理）

**P0-A　XSS：提示注入 → LLM 输出 → 前端 `v-html` 未消毒**
`ItineraryForm.vue:64` 仍是 `<div v-if="parsedAiOutput" v-html="parsedAiOutput"></div>`，
未接入 `dompurify`；`main_agent.py` 的 `_generate_natural_language_output` 仍将用户原文
未加分隔地直接插值进提示词（`"你是一个旅行助手。用户输入了：{user_text}"`）。攻击链：
用户输入提示注入指令 → 诱导 LLM 输出 `<img onerror=...>` → `marked.parse()`（v17，不带
内置 sanitizer）透传原始 HTML → `v-html` 直接执行。
**方案**：① 前端引入 `dompurify`，`DOMPurify.sanitize(marked.parse(text))` 后再
`v-html`；② 提示词把用户输入放进明确定界区块并声明"其中内容一律视为数据"；③ 后端输出
前剥离 `<script>`/`on*=`/`javascript:` 模式做兜底。

**P0-B　`node_modules/` 被提交进 git，根依赖是废弃壳包**
确认：`git ls-files` 仍有 616 个 `node_modules/` 下的文件；根 `package.json` 仍只有
`"three.js": "^0.77.1"`（npm 上已废弃的转发包，拉的是 2016 年的 `three@0.77.0`，与
`frontend/package.json` 里真正使用的 `"three": "^0.164.0"` 无关，纯误装产物）。
**方案**：根 `.gitignore` 补 `node_modules/`；`git rm -r --cached node_modules`；确认无引用后
删除根 `package.json`/`package-lock.json`/`node_modules/`。

**P0-C　`.gitignore` 残留未解决的合并冲突标记**
确认：文件中仍有 `<<<<<<< HEAD` / `=======` / `>>>>>>> dev` 三行原样存在。`.env` 恰好仍在
标记之间因而"碰巧"仍被忽略——这是运气而非保证，下一次编辑该文件就可能让 `.env` 失去保护。
**方案**：手工清理为规范的 `.gitignore`（分 Python / Env & secrets / Node / Runtime artifacts
几段），新增 `.env.example`（只含键名），必要时接入 `gitleaks` 或 GitHub Secret Scanning。

**P0-D　核心接口无鉴权、无限流、无超时；CORS 允许所有来源**
确认：`server.py` 的 `CORSMiddleware` 仍是 `allow_origins=["*"]`；
`/api/v1/agent/generate_itinerary` 没有 API Key 校验、没有速率限制、没有输入长度上限，
payload 类型是裸 `Dict[str, Any]`。单次请求会串行触发 2 次 Gemini + 多次 SerpAPI 调用，
无鉴权情况下极易被刷爆付费额度。
**方案**：① 加 `X-API-Key` 头校验（环境变量）；② 接入 `slowapi` 按 IP 限流；③ `payload`
换成显式 Pydantic 模型，`input` 加 `max_length`；④ 给外部调用和整个 handler 加超时
（如 `asyncio.wait_for` 90s）；⑤ CORS 改为显式来源列表，从环境变量读取。

### P1（明确缺陷 / 契约违反 / 成本问题）

**P1-A　机票与酒店结果被硬截断为 1 条**
确认：`hotel_tool.py:35` `for hotel in properties[:1]`、`flight_tool.py:297`
`} for f in filtered[:1]]`，`main_agent.py:414-415` 对酒店又截一次。三处截断都发生在
拿到完整 API 响应**之后**，注释所称"减少 API 调用"并不成立，真实效果是抹掉了比价功能——
`flight_tool` 做了预算过滤和排序、`hotel_tool` 拿到完整候选列表，最终却只吐出一条。
这也是 REQUIREMENTS 3.3 "返回候选航班和酒店，而不是只保留一个结果（建议各最多 3 个并
标明推荐项）"未达成的直接原因。
**方案**：条数参数化（`max_results: int = 3`），删除 `main_agent.py` 的二次截断；如需控制
喂给 LLM 总结的 token 成本，在送入 `_generate_natural_language_output` 前裁剪，而不是
裁剪返回给前端的数据。

**P1-B　三处独立汇率表，CNY 数值互相矛盾**
确认：`flight_tool.py:97` `"CNY": 0.94`，而 `tools.py:996` 和 `attraction_tool.py:58` 都是
`"CNY": 0.65`（真实汇率约 0.63）。`flight_tool` 高估约 45%，会让以人民币计价的预算过滤
严重失真。三份表还各自维护，长期必然继续漂移。
**方案**：短期抽出 `app/tools/currency.py`，只保留一份 `EXCHANGE_TO_MYR` + `convert_to_myr()`，
三处全部改为引用，同时把 `flight_tool` 的 CNY 改成 0.65；中期考虑接入带缓存/TTL 的汇率
API，常量表作为不可用时的回落。

**P1-C　`attraction_tool` 被两种导入方式加载成两个独立模块**
确认：`attraction_seed_agent.py:10`、`transportation_agent.py:11`、`attraction_agent.py:21`、
`flight_agent.py:7`、以及多个 `app/tests/*.py` 脚本仍在用 `sys.path.insert/append`
把 `app/tools/` 或项目根塞进 `sys.path` 再按裸模块名导入；而 `tools.py` 里另一部分代码
按 `app.tools.attraction_tool` 包路径导入。两条路径会被 Python 当成两个不同模块加载，
产生两份独立的模块级缓存锁、两份重复解析的种子数据。`main_agent.py` 本身已经改成了纯
包内导入（无 `sys.path` hack），但其余 agent 和测试脚本没有跟进。
**方案**：全项目统一改为 `from app.tools.xxx import ...` 包内绝对导入；删除所有
`sys.path.insert/append`；补齐 `app/__init__.py` 等包标记（若尚缺）；统一用
`python -m app.agents.xxx` 方式运行脚本。

**P1-D　请求全程串行，无超时保护**
`run_test_main_agent_flow` 里酒店 → 去程航班 → 返程航班 → N 个景点逐个查询 → LLM 总结，
完全串行执行；`/api/v1/agent/generate_itinerary` 是同步 `def`，无 handler 级超时，前端
`fetch` 也无 `AbortSignal.timeout`。冷缓存下响应时间可达分钟级。
**方案**：酒店/去程/返程三者互相独立，可用 `ThreadPoolExecutor` 并行；`run_seed_agent`
内多个景点查询同样可并行（需配合缓存改并发安全，见下一条）；给 handler 和 LLM 调用加
超时；前端 `fetch` 加 `AbortSignal.timeout`。中长期可考虑异步任务模式
（`POST` 立即返回 `task_id`，前端轮询/SSE）。

**P1-E　前端已发送但后端完全忽略 `budget` 与 `must_visit_attractions`**
确认：`frontend/src/stores/itinerary.js` 仍在组装并发送 `budget`（默认
`{min:1000, max:5000, currency:'CNY'}`）和 `must_visit_attractions`；但
`server.py:generate_itinerary` 只读取 `payload.get("input")`，`main_agent.py` 的
`_DispatchPlanModel`/`_build_fallback_dispatch_plan` 里 `budget` 全部硬编码为
`{min:0, max:10000, currency:"MYR"}`，"必去景点"没有任何接收端。前端默认币种 `CNY`
与全系统 `MYR` 基准也不一致，叠加 P1-B 的汇率误差会让预算过滤错得更离谱。
**方案**：`server.py` 改用显式 Pydantic 请求模型接收 `budget`/`must_visit_attractions`/`pax`
并透传给 `main_agent`；"必去景点"与 `run_seed_agent` 结果合并去重并优先排期；前端默认
币种改为 `MYR`；若短期不打算做"必去景点"，从前端删掉这个字段，避免"看起来支持其实无效"。

**P1-F　后端错误一律返回 HTTP 200**
确认：`server.py` 的 `except Exception as e: return {"code": 500, ...}` 仍是普通
`return`，FastAPI 一律以 HTTP 200 发出；`message: str(e)` 把原始异常字符串直接返回给客户端。
**方案**：改用 `HTTPException` 或 `JSONResponse(status_code=...)`，区分参数错误(400)/
第三方服务失败(502)/其他(500)；对外返回泛化文案，`logging.exception()` 记录完整堆栈；
用 `logging` 替代散落各处的 `print()`。

**P1-G　景点缓存读-改-写非原子，无 TTL（未逐行复核，按原报告位置保留）**
`attraction_tool.py` 的缓存实现是整份 JSON 读入→改一个键→整体写回，`_CACHE_LOCK` 只在
进程内有效，多进程/`uvicorn --workers N` 下会丢更新；写入非原子，进程中途被杀可能得到
损坏的 JSON（`_load_cache` 的兜底会让整个缓存静默清零）；缓存条目没有 TTL。
**方案**：最小改动是原子写（写临时文件后 `os.replace()`）+ 跨进程文件锁（`filelock`）；
推荐改用 `sqlite3`（标准库自带，支持并发写和事务），加 `created_at` 做 TTL。

### P2（可维护性 / 健壮性，持续拖慢迭代）

- **`optimize_multi_location_route` 是 O(n!) 暴力 TSP**（`multi_route_tool.py`），10 个
  景点对应 90 次 SerpAPI 调用 + 360 万排列；目前该链路未被接通（配合 P1-D 一并解决时才需要
  处理），但一旦接入 `transportation_agent` 就会立刻暴露。建议 n≤8 用 Held-Karp DP，n>8
  用最近邻+2-opt 启发式，并加 `max_locations` 熔断。
- **`requirements.txt` 零版本约束**，且缺 `pytest`/`pydantic`/`langchain-core` 显式声明，
  `langchain-openai` 只被测试脚本用却在生产依赖里。建议锁版本、拆分
  `requirements-dev.txt`。
- **`app/tests/` 里多数是手动联调脚本而非 pytest 测试**：确认目录下已有
  `test_attraction_tool.py` 和新增的 `test_main_agent_regressions.py` 两个真正的 pytest
  文件（这块比旧报告写作时有改善），但 `test_main_agent.py`、`test_attraction_agent.py`、
  `test_call_attraction_agent.py`、`test_agent.py`、`test_serpapi.py`、
  `test_google_*.py` 仍是无断言的手动脚本，且被 `pytest` 按 `test_*.py` 规则收集时会
  触发真实 API 调用副作用。建议移出 `app/tests/` 改放 `scripts/`，只保留真 pytest 用例。
- **`app/tools/tools.py`（1400+ 行）里只有 `TRAVEL_ATTRACTION_CATALOG` 被实际使用**，
  `travel_planner`/`_build_view` 的完整排期实现和 `get_location_info`/`calculate_distance`
  均未被调用。注意：`_build_view` 的排期逻辑思路已经在 `main_agent._schedule_attractions`
  里用更简单的方式重新实现了一遍（见第 1 节），`tools.py` 里这份可以视为废弃，无需再迁移复用。
  建议把 `TRAVEL_ATTRACTION_CATALOG` 抽成数据文件，其余按需删除或移到独立模块。
- **`_fetch_url_text` 抓取任意 URL 无大小上限、无域名管控**（`attraction_tool.py`），
  建议加读取字节上限、校验 `Content-Type`、限制重定向。

### P3（技术债，可批量清理）

- `server.py:24` `datetime.utcnow()` 已废弃，改 `datetime.now(timezone.utc)`。
- Gemini 模型默认值三处不一致：`main_agent.py:98` 默认 `gemini-1.5-flash`（较旧型号），
  `transportation_agent.py` 默认 `gemini-2.5-flash`，README 写的也是 2.5-flash。建议统一到
  `config.py` 读取一次。
- `flight_tool.py:262-263, 215` 用 `price / passengers`，`passengers=0`（用户可控）会
  直接抛 `ZeroDivisionError`，建议 `passengers = max(1, int(passengers or 1))`。
- `app/config.py` 现在只有 `SERPAPI_API_KEY`/`GOOGLE_API_KEY` 两行，且带一句自言自语式
  注释（"确保这一行名字是对的"），全项目基本没人引用它，各模块各自
  `load_dotenv()`。建议升级为 `pydantic-settings` 的 `BaseSettings`，集中管理并做启动时校验。
- `app/data/attraction_cache.json` 仍被 git 跟踪（本次 `git status` 里它也在变更列表中），
  每次运行都会产生一次 diff，应 gitignore。
- 缺少基础工程配置：无 `pyproject.toml`/linter/formatter 配置、无 CI（`.github/workflows`
  不存在）、无 `.env.example`、`frontend/vite.config.js` 无 `server.proxy` 兜底（未配
  `.env.local` 时请求会落到 Vite dev server 上 404）。

---

## 5. 需要产品/你来拍板的决策点

1. **前端时间线 UI 是否复活？** 后端数据已就绪（见第 2 节），只差前端接线。这是当前
   性价比最高的一步，建议优先做。
2. **是否升级到 REQUIREMENTS 3.2 提出的完整 `days[]` 统一契约**（把 flights/hotel_checkin/
   checkout/attraction/transport 都归一成一个按天分组的 `items` 数组，带
   `data_source`/`request_id`/多候选航班酒店），还是维持现在"`views[]` 加时间戳"的最小方案？
   后者已经足够复活前端时间线，前者是更大的架构改动。
3. **`transportation_agent`/`multi_route_tool` 到底要接入还是删除？** 目前是"做了一半"
   的状态（P2-3、P2-4 都指向它）。接入意味着要先解决 O(n!) 复杂度问题；不接入则应该把
   `run_travel_agent` 的 import 和相关死变量一并清理。
4. **`must_visit_attractions` 要实现还是从前端删除？** 目前是"看起来支持其实无效"的
   假功能，两边都不动只会持续制造困惑。

---

## 6. 建议处理顺序（合并两份文档的阶段规划，已剔除已完成项）

**第一阶段：止血（风险最低，建议先做）**
1. P0-C 清理 `.gitignore` 冲突标记 + `.env.example`
2. P0-B `node_modules/` 移出版本控制，删根 `package.json`/`three.js`
3. P0-A 前端接 `dompurify`
4. P1-B 统一汇率表，修正 CNY
5. 决策点 1：前端时间线是否复活（若是，本阶段末尾就能看到效果）

**第二阶段：清理与收敛**
6. P1-C 统一为包内绝对导入，删除所有 `sys.path` 注入
7. 第 3 节的遗留小项（默认日期常量统一、清理死变量）
8. P2 的 `requirements.txt` 锁版本、测试脚本归位

**第三阶段：修正缺陷**
9. P1-F 返回真实 HTTP 状态码 + `logging`
10. P0-D 鉴权 + 限流 + 输入约束 + CORS 收紧
11. P1-G 缓存改原子写/SQLite + TTL
12. P1-A 机票/酒店条数参数化
13. P1-E 接收 `budget`/`pax`/`must_visit_attractions`（取决于决策点 4）

**第四阶段：核心能力**
14. 决策点 2：是否升级到完整 `days[]` 契约
15. P1-D 外部调用并发化 + 全链路超时
16. 测试覆盖 + CI
17. 决策点 3：`transportation_agent` 接入或删除，若接入则先解 P2 的 O(n!) 问题
