# Better-travel-assistant 后端系统架构设计文档 v1

> 版本：v1 · 2026-08-05
> 范围：**后端 + API 契约**（前端另行设计）
> 上游文档：`docs/flight-agent/*`、`docs/hotel/*`、`docs/poi/*`、`docs/navigation/*`

> ## ⚠️ 本文档已部分失效（2026-08-12）
>
> 编排方式改成了**模型自主调工具**，原来那条 LangGraph 固定管线连同它的产物
> 一起删除了。下面这些章节描述的是已经不存在的代码：
>
> - LangGraph 图结构、节点划分、并行分支、`interrupt()` 中断问答
> - 时间窗计算、分天聚类、天内 TSP 排序、营业时间约束求解
> - `POST /trips` + SSE 事件流 + `/answer`，以及 `TripPlan` / `Itinerary` 契约
>
> **仍然有效**的部分：需求与边界、数据源与额度纪律、坐标系约定、
> 缓存/重试/熔断、错误码、记忆分层、`/trips/parse` 与 `/trips/chat` 契约。
>
> 现在的规划路径见 `app/agents/planner_agent.py` 的文件头——包括它交给模型
> 之后**失去了哪些保证**。

---

## 1. 需求与边界

### 1.1 用户故事

> 用户输入**出行时间**和**地点**（出发城市 + 目的地城市），系统自动：
> 1. 用 Flight ReAct Agent 查**往返机票**，并确定**到达机场**；
> 2. 查**目的地城市的酒店**；
> 3. 查**目的地城市的热门景点**，并生成**逐日路径规划**；
> 4. 输出一份完整的结构化行程 + 自然语言说明。

### 1.2 关键决策（本版已拍板）

| # | 决策项 | 结论 | 影响 |
|---|--------|------|------|
| D1 | 地域覆盖 | **目的地限中国大陆**；出发地不限 | 景点/路径规划全程走高德；不引入 Google Places/Directions |
| D2 | 交互形态 | **表单起手 + 关键节点人工确认**（human-in-the-loop） | 需要可中断/可恢复的编排引擎与异步 API |
| D3 | 技术栈 | **FastAPI + LangGraph + Gemini** | 编排用状态图；Flight ReAct 作为子图接入 |
| D4 | 交付范围 | 后端 + API 契约 | 本文不含前端组件设计，但定义了前端所需的全部事件与数据契约 |

### 1.3 明确的非目标（v1 不做）

- 机票 / 酒店的**实际预订与支付**（`booking` 阶段仅预留状态位）
- 海外目的地的景点与路径规划（高德 Web 服务 API 不覆盖境外）
- 多用户账号体系、行程持久化历史（v1 用 trip_id + 会话级存储）
- 前端实现

### 1.4 D1 带来的能力边界（必须在产品层面兜住）

| 能力 | Provider | 覆盖 |
|------|----------|------|
| 往返机票 | SerpAPI Google Flights | 全球（含中国国内航线） |
| 酒店 | SerpAPI Google Hotels | 全球，但**中国大陆房源密度显著低于境外**，价格多来自 Booking/Agoda/Trip.com |
| 景点 POI | 高德 POI 2.0 | 仅中国大陆 |
| 路径规划 | 高德 Direction v3/v4 | 仅中国大陆 |

**校验规则**：`intake` 节点必须校验目的地解析出的 `adcode` 属于中国大陆，否则直接返回 `DESTINATION_UNSUPPORTED`，不浪费任何 SerpAPI 额度。

---

## 2. 系统总览

```
┌──────────────────────────────────────────────────────────────────────┐
│  HTTP 层 (FastAPI)                                                    │
│  POST /trips  ·  GET /trips/{id}/stream (SSE)  ·  POST /trips/{id}/answer │
│  鉴权 (X-API-Key) · 限流 (slowapi) · 请求校验 (Pydantic)               │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  TripRequest
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  编排层 (LangGraph StateGraph, checkpointer=thread_id:trip_id)        │
│  intake → resolve_city → ┌ flight_subgraph ┐ → route_planner → summarize│
│                          └ attraction → hotel ┘                        │
│  interrupt() 在关键节点挂起，等待 /answer 恢复                          │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
        ┌───────────────┬───────┴────────┬────────────────┐
        ▼               ▼                ▼                ▼
┌──────────────┐ ┌─────────────┐ ┌──────────────┐ ┌──────────────┐
│ Flight Agent │ │ Hotel Agent │ │Attraction Ag.│ │ Route Planner│
│  (ReAct)     │ │             │ │              │ │ (确定性算法) │
└──────┬───────┘ └──────┬──────┘ └──────┬───────┘ └──────┬───────┘
       │                │               │                │
       ▼                ▼               ▼                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Tool 层（LangChain @tool，统一超时/重试/缓存/错误归一）                │
│  flights_autocomplete · flights_search · hotels_autocomplete ·        │
│  hotels_search · poi_keyword · poi_around · poi_detail ·              │
│  distance_batch · direction_transit · direction_driving · walking     │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  Provider 层（纯 HTTP 客户端，无业务逻辑）                             │
│  SerpApiClient (httpx)          AmapClient (httpx)                    │
└───────────────────────────────┬──────────────────────────────────────┘
                                ▼
┌──────────────────────────────────────────────────────────────────────┐
│  基础设施：Settings(.env) · Cache(TTL) · CoordTransform(WGS84↔GCJ02)  │
│            RateLimiter · Retry/Backoff · Logger · LLM(Gemini)         │
└──────────────────────────────────────────────────────────────────────┘
```

**分层原则**：Provider 只管发 HTTP、解 JSON、抛归一化异常；Tool 只管参数映射 + 结果裁剪（**不把原始大 JSON 喂给 LLM**）；Agent 只管决策；编排层只管状态流转与人机交互。跨层调用一律禁止（HTTP 层不得直接调 Tool）。

---

## 3. 目录骨架

```
app/
  main.py                      # FastAPI 应用装配、中间件、路由挂载
  config.py                    # Settings (pydantic-settings)，读 .env
  api/
    v1/
      routes_trips.py          # POST /trips, GET /trips/{id}, /stream, /answer
      routes_health.py
      deps.py                  # 鉴权、限流、依赖注入
      errors.py                # 统一异常 → ApiError 响应
  graph/
    state.py                   # TripState (TypedDict) + reducer
    builder.py                 # StateGraph 装配、checkpointer、编译
    nodes/
      intake.py                # 参数归一 + 日期解析 + 目的地合法性校验
      resolve_city.py          # 目的地城市 → adcode/citycode/中心坐标
      flight.py                # Flight ReAct 子图入口
      attraction.py
      hotel.py
      route_planner.py         # 确定性行程编排（非 LLM）
      summarize.py             # LLM 生成自然语言说明
  agents/
    flight_react.py            # ReAct 子图（对应 flight-react-agent-design.md）
    hotel_agent.py
    attraction_agent.py
    prompts/                   # system prompt 模板（与代码分离，可热改）
  tools/
    serpapi_flights.py         # flights_autocomplete / flights_search
    serpapi_hotels.py          # hotels_autocomplete / hotels_search
    amap_poi.py                # poi_keyword / poi_around / poi_detail
    amap_route.py              # distance_batch / transit / driving / walking
    registry.py                # Tool 注册表 + 统一装饰器（缓存/重试/裁剪）
  providers/
    serpapi_client.py
    amap_client.py
    llm.py                     # Gemini 客户端工厂
  models/
    trip.py                    # TripRequest / TripPlan / DayPlan ...
    flight.py                  # Airport / FlightLeg / FlightItinerary ...
    hotel.py                   # HotelCandidate ...
    attraction.py              # Attraction ...
    route.py                   # RouteLeg / TransitDetail ...
    events.py                  # SSE 事件与 InterruptQuestion
  core/
    geo.py                     # WGS84 ↔ GCJ-02、haversine、centroid、聚类
    dates.py                   # 相对日期解析、时间窗计算
    cache.py                   # TTL 缓存（内存 → 可换 Redis）
    retry.py                   # 指数退避 + 熔断
    logging.py
  tests/
    unit/                      # 纯函数：geo / dates / 解析器 / 规划算法
    contract/                  # 用固定 fixture 校验 Provider 解析
    e2e/                       # 全 mock provider 跑通整图
docs/
  architecture/system-architecture.md   # ← 本文
```

---

## 4. 编排设计（LangGraph）

### 4.1 全局状态 TripState

```python
# app/graph/state.py
from typing import Annotated, Literal, Optional, TypedDict
from operator import add

class TripState(TypedDict, total=False):
    # ---- 输入（intake 归一后写入，全程只读）----
    trip_id: str
    request: TripRequest              # 见 §7.1
    locale: LocaleCtx                 # {gl:"cn", hl:"zh-CN", currency:"CNY"}——全程恒定

    # ---- 目的地解析 ----
    dest_city: CityRef                # {name, adcode, citycode, center_gcj02}

    # ---- 各分支产出 ----
    flight: FlightBranch              # {candidates, selected, arrival_airport, arrive_at, depart_at}
    attractions: AttractionBranch     # {pool, selected, centroid_gcj02}
    hotel: HotelBranch                # {candidates, selected}
    itinerary: Optional[Itinerary]    # route_planner 产出
    summary: Optional[str]            # summarize 产出

    # ---- 控制面 ----
    phase: Literal["intake","flight","attraction","hotel","planning","done","failed"]
    pending: Optional[InterruptQuestion]     # 当前挂起的问题
    warnings: Annotated[list[Warning], add]  # 降级/兜底记录，最终回传前端
    errors:   Annotated[list[ApiError], add]
    quota:    QuotaCounter                   # 本次规划各 provider 调用计数
```

`warnings` / `errors` 用 `Annotated[list, add]` 做 reducer，保证并行分支各自追加不互相覆盖——这是并行分支下唯一安全的写法，其余字段各分支写各自的 key，不产生并发冲突。

### 4.2 节点图与依赖

```
                    START
                      │
                   [intake]                  参数归一 / 日期解析 / 目的地必须在大陆
                      │
                 [resolve_city]              高德 POI → adcode, citycode, 城市中心(GCJ-02)
                      │
        ┌─────────────┴──────────────┐       ← 扇出（并行）
        ▼                            ▼
 [flight_subgraph]            [attraction_search]
  ReAct，见 §5.1               POI 关键字+周边 → 打分 → Top-K
        │                            │
   ⏸ 选机场 / 选方案            ⏸ 必去 / 排除（可选中断）
        │                            ▼
        │                      [hotel_search]     用景点重心重排候选
        │                            │
        │                       ⏸ 选酒店
        └─────────────┬──────────────┘       ← 汇合
                      ▼
              [route_planner]                分天 + 天内排序 + 距离矩阵 + 路线明细
                      │
                [summarize]                  Gemini 生成自然语言行程说明
                      │
                     END
```

**为什么 hotel 在 attraction 之后**：酒店的好坏在行程里主要体现为"离要去的景点近不近"。先拿到景点集合，算出**景点重心**，再用 `amap_distance_batch`（1 次调用，≤100 起点）把酒店候选按到重心的真实驾车时长重排。代价是 hotel 无法与 attraction 并行，但这两步都是秒级，而 flight 分支（最慢，含多轮 ReAct + 用户确认）是并行的，整体关键路径没有变长。

**为什么 route_planner 不是 LLM**：分天聚类、时间窗约束、最近邻排序是确定性优化问题，LLM 做这个既慢又不稳定（会编造距离和时间）。v1 用确定性算法产出行程骨架，只把**自然语言解释**交给 LLM（`summarize`）。

### 4.3 人机交互（interrupt / resume）

```python
# 节点内部
from langgraph.types import interrupt

choice = interrupt(InterruptQuestion(
    id="flight.departure_airport",
    kind="single_choice",
    title="纽约有 3 个机场，从哪个出发？",
    options=[{"key":"JFK","label":"[JFK] John F. Kennedy Intl · 距市中心 14 mi"}, ...],
    default="JFK",
).model_dump())
```

- 图编译时挂 checkpointer，`thread_id = trip_id`；`interrupt()` 会把状态落盘并中止执行。
- **`pending` 是列表**：两条分支并行，可能在同一个 superstep 各中断一次（实测：
  `flight_arrival` 与 `hotel_search` 都在 S2，会同时挂起两个问题）。
- **恢复必须按 id 定向**：多个中断挂起时 langgraph 强制要求
  `Command(resume={interrupt_id: value})`，不带 id 会直接抛 RuntimeError。那个
  `interrupt_id` 是内部哈希，不该进 API 契约——`TripRunner` 每次从当前快照现场重建
  `question.id → interrupt_id` 映射，因此进程重启后（只要 checkpointer 还在）依然有效。
- **超时策略**：`pending` 带 `expires_at`（默认 10 分钟）。`TripService.start_sweeper()`
  在 lifespan 里起一个循环任务（周期 = 超时 / 4，下限 15 秒），扫出过期问题后用 `default`
  值自动恢复并记 `ANSWER_TIMED_OUT` 警告，避免行程永久卡死。
  `expires_at` 只是个时间戳——**没有这个清扫任务，超时机制等于不存在**：用户看到
  「选哪个机场」直接关掉页面，行程会永远停在 `waiting_input`，thread 也永不释放。
  记账（`TripRunner.note_expired()`）与恢复动作是拆开的：脚本走阻塞的 `resume_expired()`，
  服务层必须走流式的 `resume_stream()`，否则还挂在 SSE 上的客户端看不到后续进度。
- ⚠️ **不要用 `aupdate_state` 往挂起的线程里写东西**：它会把待恢复的中断一并清掉，
  紧接着的 resume 就找不到问题了。超时警告因此记在 `TripRunner` 自己账上（同 `quota`），
  在返回 state 时合并——代价是这两项不进 checkpointer，多 worker 时要一起挪到共享存储。

**并行下的状态写入纪律**：两条分支同一 superstep 写同一个 key 会直接抛
`InvalidUpdateError`。除了 `warnings`/`errors` 用 `add` reducer 外，`phase` 与 `status`
也必须带 reducer（进度取更靠后者、失败压过一切）——这是实测踩到的，不是理论风险。

**汇合节点还需要自己的守卫**：一条分支失败时，另一条分支的条件边未必看得到那次写入，
`route_planner` 会被触发。它开头直接检查 `errors` 并返回空补丁——别再花额度编排一份
用不上的行程。
- **v1 的三个中断点**（其余全部自动决策）：

| 中断点 | 触发条件 | 跳过条件 |
|--------|----------|----------|
| `flight.departure_airport` / `flight.arrival_airport` | Autocomplete 该城市返回 **≥2 个机场** | 只有 1 个机场 → 自动选中并记 `warning` |
| `flight.itinerary` | `best_flights` 有 ≥2 条 | 用户在表单勾了"自动选最优" → 取 best_flights[0] |
| `hotel.selection` | 重排后 Top-3 | 表单勾了"自动选" → 取 Top-1 |

景点默认**不中断**（表单里的 `must_visit` / `avoid` 已经表达了偏好），仅当解析出的候选 < 期望天数 × 2 时才中断询问是否放宽筛选。

### 4.4 检查点存储

| 环境 | Checkpointer | 说明 |
|------|--------------|------|
| 开发 | `MemorySaver` | 进程内，重启即失效 |
| 生产 | `SqliteSaver`（单机）/ `PostgresSaver`（多副本） | 必须持久化，否则多 worker 下 `/answer` 可能路由到没有该 thread 的进程 |

**部署约束**：v1 若用 `MemorySaver`，uvicorn 必须 `--workers 1`；上多 worker 前必须先换持久化 checkpointer。

---

## 5. Agent 设计

### 5.0 需求解析（`agents/prompt_parser.py`）

自然语言 → `TripRequest` 草稿。**在图之外**：`POST /trips/parse` 单独调用，产出草稿给
用户确认，确认后再走 `POST /trips`。这正是 D2「表单起手 + 关键节点确认」的形态——
自然语言只是替用户把表单填好，最终提交的仍然是结构化参数。

两层：

| 层 | 职责 | 失败时 |
|---|---|---|
| 抽取 | 从原话里摘字段。LLM 优先，规则正则兜底 | 模型挂了/返回垃圾 → 退回规则抽取，`degraded=true` |
| 归一 | 短语落成绝对日期、补默认值、拼 `TripRequest` | 纯函数，缺什么进 `missing`/`questions`，绝不抛异常 |

⚠️ **模型只摘短语，日期一律由代码算。** 抽取层输出的是 `"下周三"` 这样的原话，
落成绝对日期走 `core.dates.parse_relative_date`。LLM 做日期算术出了名地不可靠，
而算错日期意味着整条链路去查**错日子**的机票——错得既贵又不显眼。
同理，农历节日（春节/清明/端午/中秋）不猜，返回追问；公历固定的
元旦/五一/国庆才直接落成日期。

每个字段都带 `origin`（`prompt` 原话 / `derived` 推算 / `default` 默认值），
用户要能一眼看出哪些是自己说的、哪些是系统替它定的。

### 5.1 Flight 分支（三个节点）

> ⚠️ **这里没有 LLM 循环，是刻意的。** `flight-react-agent-design.md` 的 Thought/Action 循环
> 是为对话式交互设计的——Agent 要逐轮追问出发地、目的地、日期、人数、舱位。但 D2 决策选了
> 「表单起手」，这些参数进入本分支时已经齐全，ReAct 循环剩下的只有「机场有歧义时问用户」和
> 「搜不到时换条件重试」，两者都是确定性逻辑。用 LLM 跑这段只会带来延迟、成本和不确定性。
> 将来要做对话式模式时，ReAct 循环应加在本分支**之上**（负责把自然语言熬成
> `FlightSearchParams`），然后仍然复用这里的函数。

拆成三个节点而不是一个：LangGraph 在 resume 时会**从头重放整个节点**，一个节点里放两个
`interrupt()` 会让重放语义难以推理。拆开后每个节点最多一个中断点，resume 的对应关系一目了然；
重放时的 API 调用由本地 TTL 缓存吸收，不重复消耗额度（计入 `quota.cache_hits`）。

| 节点 | 职责 | 中断点 |
|------|------|--------|
| `flight_departure` | 出发城市 → 机场候选 | `flight.departure_airport`（≥2 个机场时） |
| `flight_arrival` | 目的城市 → 机场候选，补齐日期/人数/舱位 | `flight.arrival_airport` |
| `flight_search` | 搜索 + 兜底 + 返程时刻 | `flight.itinerary`（≥2 个方案时） |

用户直接填 IATA 三字码时跳过 `flights_autocomplete`，省一次额度。

**兜底重试链**（与上游文档 §7 的建议有出入，理由如下）：

1. **放宽舱位** —— 严格是原条件的超集，不会给出用户没要的东西；
2. **换同城备选机场** —— 仍在同一个城市，是用户会自己做的调整；
3. 到此为止，报 `NO_FLIGHTS`。

上游文档还建议「日期 ±3 天」，这里**故意不做**：悄悄挪动出行日期会改掉行程本身（酒店、请假、
同行人都对不上），属于用户才能拍板的事。上限 4 次尝试——每次尝试烧 1 次 SerpAPI 额度。

**返程起飞时刻需要第二次查询**：SerpAPI 往返搜索的 `best_flights` 里**只有去程航段**，
要拿返程必须带选定去程的 `departure_token` 再查一次。这个时间是 `route_planner` 的硬依赖
（末日行程必须在返程起飞前收尾）。拿不到时退回「返程日 09:00」的保守假设并记 `warning`——
宁可少排半天，也不能给出一份「飞机起飞后还在逛景点」的行程。

**每次规划的航班额度成本：4 次**（2 次机场补全 + 1 次去程 + 1 次返程），
免费额度 250/月 ≈ 60 次完整规划。

**参数完备性检查**沿用 `flight-data-specification.md` §5 的 `isParamsReady`，在 Python 侧实现为
Pydantic 属性，不依赖 LLM 判断；不完整时直接抛 `INVALID_PARAMS`，绝不发出一个必然返回空结果
的请求。

### 5.2 Hotel Agent

两段式，对应 `docs/hotel/` 两份文档：

1. **锚点构造**：`q = f"{dest_city.name} 酒店"`；若 `attractions.centroid` 落在某个商圈内（`poi[].business.business_area`），则 `q = f"{城市}{商圈名}附近酒店"`，命中率更高。
2. **搜索**：`hotels_search(q, check_in_date, check_out_date, adults, children, children_ages, gl="cn", hl="zh-CN", currency="CNY", sort_by, min_price/max_price, hotel_class, rating)`。
   - 预算映射：`TripRequest.budget_per_night` → `max_price`；未填则不传（不要瞎设上限，空结果是最常见的翻车原因）。
   - **`ads[]` 与 `properties[]` 混合参与候选池**，`ads` 打 `is_ad=true` 标签透传给前端（上游文档 §7.11：ads 常常更便宜且同样带 `property_token`）。
3. **重排**（本项目的增量价值）：
   - 取候选的 `gps_coordinates`（**WGS-84**）→ `wgs84_to_gcj02` → 作为 `origins` 调一次 `amap_distance_batch(destination=attractions.centroid, type=1)`；
   - 综合分 `score = 0.45·价格分 + 0.30·评分分 + 0.25·通勤分`（各维 min-max 归一，通勤分取负相关）；
   - 输出 Top-3 供用户选择。
4. **降级**：若 Google Hotels 在该城市返回空（大陆中小城市常见），退回 `amap_poi_keyword(keywords="酒店", region=城市, types=100000, show_fields=business)`，产出**无房价但有坐标/评分/电话**的候选，并写 `warning: HOTEL_PRICE_UNAVAILABLE`。行程规划只需要坐标，因此这个降级不阻断主流程。

**币种/地区一致性**：`gl/hl/currency` 在 `intake` 阶段一次性写入 `TripState.locale`，Hotel 的 autocomplete 与 search 必须复用同一份（上游文档 §7.4 的坑）。

### 5.3 Attraction Agent

> ⚠️ 本节的召回策略经过实测修正。**不要传 `keywords`**——原方案（关键字 `景点/博物馆/公园`
> ＋ 周边补充检索）在杭州跑出来把西湖排到第 6，前几名是崇一堂、江堤步道这类冷门 POI。

**对照实验（杭州，2026-08-05 实测）**：

| 调用方式 | 返回顺序（前 6） |
|---------|-----------------|
| `types` only，不传 keywords ✅ | 千岛湖 → 西湖 → 西溪湿地 → 灵隐寺 → 飞来峰 → 雷峰塔 |
| `keywords=景点` ❌ | 钱江世纪公园 → 清河坊 → 钱江新城灯光秀 → 五柳巷 → 胡雪岩旧居 → 断桥残雪 |
| `poi_around(sortrule=weight)` ❌ | Do都城 → 市民广场 → 中粮会客厅 → 自由城 → 波浪文化城 |
| `poi_around(sortrule=distance)` ❌ | 市民中心打卡点 → Do都城 → 云戟 → 啪嗒公园 |

原因：传 `keywords` 时高德做的是**文本匹配**，会捞出名字里带该词的 POI 以及搜索区域中心
附近的东西；不传 `keywords` 时才按 **POI 权重（知名度）** 排序。周边搜索无论哪种 `sortrule`
都锚定在行政中心——而中国城市的行政中心往往是现代 CBD，不是旅游核心区。

1. **主检索**：`poi_keyword(keywords="", types="110000|110101|110200|110300", region=城市,
   city_limit=true, show_fields="business,photos,navi", page_size=25)`，取前 2 页 = 50 条
   **按知名度排序**的候选，名次记为 `recall_rank`。
2. **必去项**：`must_visit` 里的每个名字单独 `poi_keyword(keywords=名字, ...)`——这种场景下
   文本匹配正是我们要的。命中的强制置顶且**必须进入最终行程**。
3. **打分排序**：
   ```
   score = 0.35·知名度(recall_rank) + 0.25·rating分 + 0.15·类型权重
         + 0.15·距城市中心分 + 0.10·有照片/有营业时间
   ```
   知名度主导：高德评分普遍挤在 4.2~4.9 区分不开，单靠 rating 排不出好坏。
   `rating` / `cost` / `opentime_week` 必须靠 `show_fields=business` 拿（上游文档 §11.4 的坑）。
4. **子景点去重**：高德会把「西湖风景名胜区」和它的「断桥残雪」「柳浪闻莺」作为独立 POI
   返回，三者 `parent` 指向同一景区。所属景区已入选时跳过子景点——否则一个西湖能占掉
   16 个名额里的 3 个，而用户实际上只是「去西湖玩一天」。
5. **产出**：Top-K（K = 游玩天数 × 4，上限 20）的 `Attraction[]` + 景点重心 `centroid_gcj02`，
   坐标全部是 GCJ-02，直接可喂路径规划。
6. **导航点校正**：有 `navi.entr_location` 的用入口坐标替代 POI 中心坐标——大型景区中心点
   常在湖里/山里，直接拿去算驾车路线会得到荒谬结果。重心必须在校正**之后**再算。

**已知遗留**：知名度排序会把远郊景区（如千岛湖距杭州市区 140km）排进候选。距城市中心分
只占 0.15，压不住。这个交给 route_planner 的每日时间窗去裁——放不下就进 `unscheduled[]`。

### 5.4 Route Planner（确定性算法，非 LLM）

输入：`flight`（落地/返程时间、机场坐标）、`hotel.selected`（坐标）、`attractions.selected`、`request.pace`。

**Step 1 · 划分每日时间窗**

| 日 | 起 | 止 |
|----|----|-----|
| Day 1（落地日） | 落地时间 + 机场→酒店通勤 + 入住 60min | 21:00 |
| Day 2 … N-1 | 09:00 | 21:00 |
| Day N（返程日） | 08:30 | 返程起飞 − 120min（国内值机/安检 buffer）− 酒店→机场通勤 |

若 Day 1 剩余可用时长 < 3h，则该日不排景点，只排"机场→酒店 + 酒店周边晚餐/夜景"。

**Step 2 · 分天聚类**
以酒店为原点，把景点投影到极坐标 `(距离, 方位角)`，按方位角做 k=游玩天数 的扇形聚类（同方向的景点排在同一天，避免来回横穿城市）。必去景点先落位，其余按分数填充。

**Step 3 · 天内排序**
以酒店为起终点做最近邻 + 2-opt 改进的小规模 TSP（每天 ≤6 个点，暴力可解），并施加约束：
- `business.opentime_today` 未开门/已闭园的时段不可安排；
- 单点停留时长 = `pace` 决定的默认值（宽松 150min / 标准 120min / 紧凑 90min），大型景区（`typecode=110000` 且有 `children[]`）×1.5；
- 累计超出时间窗则把尾部景点顺延到下一天，仍放不下则移入 `unscheduled[]` 回传前端。

**Step 4 · 距离与路线**

> ⚠️ 本节经实现修正。原方案写「粗排用 `distance_batch`，每天 1 次调用拿到距离矩阵」——
> 但 `/v3/distance` 是**多起点 → 单终点**的向量接口，拿不到 N×N 矩阵；真要凑齐矩阵得
> 每天 N+1 次调用。

- **粗排**用 haversine 直线距离在本地求解 TSP：零额度、零延迟，而且同一天的景点本来就
  被扇形聚类聚在一起，直线距离与实际路网的**相对顺序**高度一致；
- **细化**只对最终选定的相邻点对调用一次明细路线——那也正是要展示给用户的内容：
  - `request.transport = "transit"`（默认）→ `amap_direction_transit(origin, destination, city=citycode, extensions=all, strategy=0, date, time)`，取 `transits[0]` 的 `duration/cost/walking_distance/segments 摘要`；
  - `= "driving"` → `amap_direction_driving(strategy=10, extensions=all)`，取 `distance/duration/tolls/taxi_cost/restriction`；
  - 两点直线距离 < 1.2km → 直接走 `amap_direction_walking`，不浪费公交查询。
- **配额纪律**（上游文档 §10.11）：绝不对每个景点**对**都调路线；每天的路径调用数 =
  当天景点数 + 1（含往返酒店），4 天行程约 20 次。批量「多点到一点」的比较（如酒店重排）
  一律走 `distance_batch`。

**两轮排期**：第一轮用直线估算把景点塞进时间窗，第二轮用实测路线重算时刻。
第二轮**必须重新施加营业时间约束**——实测比估算快时会把游览提前，一个 14:00 开门的景点
可能被排到 13:00（这是实现时真踩到的 bug，现由 `fit_visit()` 统一处理两轮）。
实测比估算慢导致装不下时，尾部景点移入 `unscheduled[]`：宁可少排，也不给一份跑不完的行程。

**Step 5 · 产出** `Itinerary`：`days[].items[]`（景点/酒店/机场三种 `kind`）+ `days[].legs[]`（点间交通）+ `unscheduled[]` + `totals`（总通勤时长/预估交通花费/总门票参考价）。

### 5.5 Summarize

把 `Itinerary` 压成精简 JSON（**不含 polyline、不含原始 API 响应、不含候选池**）喂给
Gemini，产出中文行程说明。System prompt 硬约束：
- 只允许使用给定 JSON 里的数字和名称，**不得编造**任何时间、价格、航班号、景点；
- 输出为纯文本 + 轻量 Markdown，不得输出 HTML；
- 长度控制在 400 字以内。

**prompt 不是约束，代码才是**：输出一律过一遍 `strip_markup()` 去标签。模型跑偏、
或被景点名里的注入内容带跑时照样会吐 HTML，前端直接渲染就是 XSS。

**确定性模板兜底**：LLM 失败**绝不能**让整次规划失败——行程本身早已排好，说明文案
只是包装。`render_fallback()` 用模板生成，每个数字都直接取自 `Itinerary`，不做任何推断，
并附 `SUMMARY_FALLBACK` 警告。

> ⚠️ **Gemini 在中国大陆网络不可用**：直连返回
> `FAILED_PRECONDITION: User location is not supported`，连 `models.list` 都调不通。
> 也因此 `LLM_MODEL` 的默认值 `gemini-3.5-flash` **未经验证**——换模型只需改环境变量。
> 在这类环境里把 `LLM_ENABLED=false` 关掉，可省下每次 30 秒的超时等待，
> 结果与模板路径完全一致。

---

## 6. Tool 层清单

所有 Tool 经 `tools/registry.py` 的统一装饰器包裹：**超时 → 重试(指数退避) → TTL 缓存 → 结果裁剪 → 异常归一化**。

| Tool | Provider | 关键入参 | 返回给 LLM 的裁剪结果 | 单次额度 |
|------|----------|----------|----------------------|----------|
| `flights_autocomplete` | SerpAPI | `q` | `[{city, airports:[{id,name,distance}]}]` | 1 |
| `flights_search` | SerpAPI | `departure_id, arrival_id, outbound_date, return_date, type=2, adults, travel_class, currency, hl` | `best_flights` 前 3 + `other_flights` 前 3，每条只留 价格/总时长/中转数/各段(航班号,航司,机型,起降时间,舱位)/碳排放 | 1 |
| `hotels_autocomplete` | SerpAPI | `q, gl, hl, currency` | `[{type, name, property_token?, autocomplete_suggestion}]` | 1 |
| `hotels_search` | SerpAPI | `q \| property_token, check_in_date, check_out_date, adults, children, children_ages, gl, hl, currency, sort_by, max_price, hotel_class, rating` | `properties` + `ads` 合并后前 10，每条留 名称/星级/评分/评论数/`total_rate`/`rate_per_night`/`gps_coordinates`/前 6 个 amenities/`property_token` | 1（翻页另计） |
| `poi_keyword` | 高德 | `keywords, region, city_limit, types, show_fields, page_size` | `[{id,name,location,typecode,address,rating,cost,opentime_today,photo?}]` | 1 |
| `poi_around` | 高德 | `location, radius, types, sortrule, show_fields` | 同上 + `distance` | 1 |
| `poi_detail` | 高德 | `id`（≤20 批量） | 含 `navi.entr_location`、`children[]`、`opentime_week` | 1 |
| `distance_batch` | 高德 | `origins`(≤100), `destination`, `type` | `[{origin_id, distance_m, duration_s}]` | 1 |
| `direction_transit` | 高德 | `origin, destination, city, cityd?, extensions=all, strategy, date, time` | `transits[0]`：总时长/票价/步行距离/换乘线路名列表 | 1 |
| `direction_driving` | 高德 | `origin, destination, strategy=10, extensions=all` | 前 1 条：距离/时长/过路费/打车费/限行 | 1 |
| `direction_walking` | 高德 | `origin, destination` | 距离/时长 | 1 |

**裁剪是硬性要求**：Google Flights 单次响应可达数百 KB，高德 `polyline` 单条路线上万字符。原始响应只写日志与缓存，**进入 LLM 上下文的必须是裁剪后的结构**，否则 token 成本和幻觉风险同时爆炸。

### 6.1 配额预算（一次完整规划）

| Provider | 典型调用数 | 免费额度 | 结论 |
|----------|-----------|---------|------|
| SerpAPI | 2~5 次（flights autocomplete ×0~2 + search ×1 + hotels autocomplete ×0~1 + search ×1） | 250 次/月 | **瓶颈**：约 50~125 次完整规划/月。演示够用，上线必须升配 |
| 高德 | 15~35 次（城市解析 1 + POI 3~6 + detail 1~2 + distance_batch 1+N天 + direction 4N） | 5000 次/日，QPS 50 | 宽裕 |

**省额度的三条纪律**：① 不主动 `no_cache=true`，SerpAPI 同参数 1 小时缓存命中不扣额度；② 本地对 `(q, dates, adults, currency)` 再做一层 TTL=1h 缓存，把同一用户反复刷新挡在外面；③ 翻页只在用户明确要"更多"时才发。

---

## 7. 数据契约（Pydantic v2）

### 7.1 输入

```python
class TripRequest(BaseModel):
    departure_city: str                       # "北京" 或 IATA "PEK"
    destination_city: str                     # 必须解析到中国大陆
    outbound_date: date
    return_date: date                         # v1 强制往返
    adults: int = 1
    children: int = 0
    children_ages: list[int] = []             # len 必须 == children
    travel_class: Literal["economy","premium_economy","business","first"] = "economy"
    budget_per_night: int | None = None       # CNY，映射到 hotels max_price
    hotel_class: list[Literal[2,3,4,5]] = []
    must_visit: list[str] = []                # 强制进入行程的景点名
    avoid: list[str] = []
    pace: Literal["relaxed","standard","packed"] = "standard"
    transport: Literal["transit","driving","walking"] = "transit"
    auto_select: bool = False                 # true = 全自动，不产生任何中断

    @model_validator(mode="after")
    def check(self):
        assert self.return_date > self.outbound_date, "return_date must be after outbound_date"
        assert len(self.children_ages) == self.children, "children_ages length must equal children"
        return self
```

### 7.2 核心输出

```python
class GeoPoint(BaseModel):
    lng: float
    lat: float
    crs: Literal["GCJ02","WGS84"] = "GCJ02"   # 显式标注坐标系，禁止裸浮点数传递

class Attraction(BaseModel):
    poi_id: str
    name: str
    location: GeoPoint                         # GCJ-02
    entrance: GeoPoint | None = None           # navi.entr_location，优先用于导航
    typecode: str
    address: str
    rating: float | None = None
    ticket_cost: float | None = None           # business.cost
    opentime_today: str | None = None
    photos: list[str] = []
    suggested_duration_min: int                # 由 pace + 景区规模推导

class HotelCandidate(BaseModel):
    property_token: str | None
    name: str
    is_ad: bool = False
    hotel_class: int | None = None
    overall_rating: float | None = None
    reviews: int | None = None
    total_rate_cny: float | None = None        # total_rate.lowest（含税估算，优先展示）
    rate_per_night_cny: float | None = None
    location: GeoPoint                         # 已转成 GCJ-02
    amenities: list[str] = []
    commute_to_centroid_min: int | None = None # 重排依据
    score: float

class RouteLeg(BaseModel):
    from_ref: str                              # item id
    to_ref: str
    mode: Literal["transit","driving","walking"]
    distance_m: int
    duration_min: int
    cost_cny: float | None = None              # 公交票价 / 过路费
    taxi_cost_cny: float | None = None
    detail: str | None = None                  # "地铁2号线 → 换乘10号线，步行 850m"

class DayItem(BaseModel):
    kind: Literal["airport","hotel","attraction","meal"]
    ref_id: str
    name: str
    location: GeoPoint
    start_time: time
    end_time: time

class DayPlan(BaseModel):
    day_index: int
    date: date
    window: tuple[time, time]
    items: list[DayItem]
    legs: list[RouteLeg]
    total_commute_min: int

class Itinerary(BaseModel):
    days: list[DayPlan]
    unscheduled: list[Attraction] = []          # 放不下的景点，前端可提示"备选"
    totals: dict                                # {commute_min, transport_cost_cny, ticket_cost_cny}

class TripPlan(BaseModel):
    trip_id: str
    status: Literal["running","waiting_input","done","failed"]
    request: TripRequest
    flights: FlightBranch | None = None
    hotel: HotelBranch | None = None
    attractions: list[Attraction] = []
    itinerary: Itinerary | None = None
    summary: str | None = None
    warnings: list[Warning] = []
    error: ApiError | None = None
```

`FlightItinerary` / `FlightLeg` / `Airport` / `Layover` / `CarbonEmissions` **直接沿用** `docs/flight-agent/flight-data-specification.md` §2.9 的定义，一字不改，只做 TypeScript → Pydantic 的等价翻译。

### 7.3 统一错误

```python
class ApiError(BaseModel):
    code: str          # 见下表
    message: str       # 技术信息，进日志
    user_message: str  # 面向用户的中文提示
    retriable: bool = False
```

| code | 触发 | user_message 示例 |
|------|------|------------------|
| `DESTINATION_UNSUPPORTED` | 目的地不在中国大陆 | 当前版本的景点与路线规划仅支持中国大陆城市 |
| `CITY_NOT_FOUND` | 高德解析不到城市 | 没找到这个城市，换个说法试试？ |
| `NO_FLIGHTS` | 兜底重试后仍为空 | 这个日期没搜到航班，建议前后调整 3 天或换个机场 |
| `NO_HOTELS` | 主搜 + 高德降级都空 | 该城市暂时查不到可订酒店 |
| `UPSTREAM_TIMEOUT` | Provider 超时 | 数据源响应较慢，正在重试… |
| `QUOTA_EXCEEDED` | SerpAPI 额度耗尽 | 今日查询次数已用完 |
| `INVALID_PARAMS` | Pydantic 校验失败 | 具体字段提示 |
| `ANSWER_MISMATCH` | resume 的答案不在选项内 | 请从给出的选项中选择 |

---

## 8. HTTP API 契约

Base：`/api/v1`　认证：`X-API-Key` 头　限流：per-IP，默认 `10/min` 创建、`60/min` 查询

### 8.1 创建规划

```
POST /api/v1/trips
Content-Type: application/json
X-API-Key: <key>

{ ...TripRequest }
```

```
202 Accepted
{
  "trip_id": "trp_01J8XK...",
  "status": "running",
  "stream_url": "/api/v1/trips/trp_01J8XK.../stream"
}
```

### 8.2 事件流（SSE）

```
GET /api/v1/trips/{trip_id}/stream
Accept: text/event-stream
```

事件类型：

| event | data | 说明 |
|-------|------|------|
| `stage` | `{"phase":"flight","label":"正在搜索往返航班…"}` | 阶段推进，驱动前端进度条 |
| `partial` | `{"key":"attractions","value":[...]}` | 分支完成即推，前端可提前渲染 |
| `question` | `InterruptQuestion` | 需要用户决策，前端弹出选择器 |
| `warning` | `Warning` | 降级提示（如"该城市房价数据不可用，已改用地图数据"） |
| `done` | `TripPlan` | 终态，连接关闭 |
| `error` | `ApiError` | 终态，连接关闭 |

```jsonc
// event: question
{
  "id": "flight.arrival_airport",
  "kind": "single_choice",
  "title": "上海有 2 个机场，降落在哪个？",
  "options": [
    {"key":"PVG","label":"[PVG] 浦东国际机场 · 距市中心 30 km"},
    {"key":"SHA","label":"[SHA] 虹桥国际机场 · 距市中心 13 km"}
  ],
  "default": "PVG",
  "expires_at": "2026-08-05T10:30:00Z"
}
```

**断线重连**：SSE 支持 `Last-Event-ID`；服务端为每个 trip 保留最近 200 条事件（内存环形缓冲），重连时补发。

### 8.3 回答中断

```
POST /api/v1/trips/{trip_id}/answer
{ "question_id": "flight.arrival_airport", "value": "PVG" }
```

```
200 { "status": "running" }
409 { "code": "ANSWER_MISMATCH", ... }   // question_id 不匹配当前 pending，或 value 不在选项内
```

幂等：同一 `question_id` 重复提交，第二次返回 `409` 而不是重复恢复图执行。

### 8.4 查询与健康检查

```
POST /api/v1/trips/parse       → 200 TripDraft（一句话 → TripRequest 草稿，见 §5.0）
GET  /api/v1/trips/{trip_id}   → 200 TripPlan（任意时刻的快照，含 status）
GET  /health                   → 200 {"status":"ok","providers":{...},"auth":...,"cache":{...}}
```

`/health` **不真发任何上游请求**——探活把 SerpAPI 月额度烧掉是本末倒置。它只报告
「密钥配没配、缓存多大」，真实可用性由业务调用的日志体现。
`providers.llm` 按当前 `LLM_PROVIDER` 查对应的那个 key（`gemini` 看 `GOOGLE_API_KEY`，
否则看 `LLM_API_KEY`）；`LLM_ENABLED=false` 时报 `disabled`，那是正常状态不是故障。

**限流**：创建 `rate_limit_create`（默认 10/分钟），其余走 `rate_limit_read`（默认 60/分钟）。
创建必须限得更严——**每次创建烧 5 次 SerpAPI**，按读接口速率放行，一分钟就能把
250 次/月的免费额度打掉 1.2 倍。`limiter` 放在 `api/limits.py` 而不是 `main.py`，
否则路由装饰器与 `main` 会循环引用。

---

## 9. 横切关注点

### 9.1 坐标系（最容易翻车的一处）

| 来源 | 坐标系 |
|------|--------|
| 高德 POI `location`、Direction、Distance | **GCJ-02** |
| SerpAPI Google Hotels `gps_coordinates` | **WGS-84** |

**规则**：`core/geo.py` 提供 `wgs84_to_gcj02` / `gcj02_to_wgs84`；`GeoPoint` 模型带 `crs` 字段强制显式标注；**任何进入高德接口的坐标必须是 GCJ-02**。Google 侧坐标不转就直接拿去规划路线，会有 300~600m 的系统性偏移——足以把酒店定位到马路对面的另一个街区，且不会报错，只会静默给出错误路线。

### 9.2 配置与密钥

```
SERPAPI_KEY=...      # SerpAPI（flights + hotels）
AMAP_KEY=...         # 高德 Web 服务 API（必须是「Web 服务」类型，JS/Android Key 会报 10001）
GOOGLE_API_KEY=...   # Gemini
APP_API_KEY=...      # 本服务对外鉴权（新增）
LLM_MODEL=...        # Gemini 型号，可配置
```

⚠️ **两条必须处理的安全事项**：
1. **`.gitignore` 目前不在工作区**（已从 HEAD 删除），`.env` 处于"未被忽略"状态——下一次 `git add -A` 会把三把真实 key 提交进历史。落地第一步就恢复 `.gitignore` 并写入 `.env`。
2. **`docs/` 里多份文档正文明文写了 `SERPAPI_KEY` 的真实值**（`flight-agent-architecture.md` §6、`serpapi-google-hotels-api.md` §2.2 等）。这些 docs 目前尚未入库，但只要提交就等于把 key 写进 git 历史。建议：提交前把文档里的 key 替换成 `<从 .env 读取 SERPAPI_KEY>`，并**轮换一次 SerpAPI Key**。

### 9.3 缓存与重试

| 对象 | 策略 |
|------|------|
| SerpAPI 响应 | 服务端 1h 缓存（SerpAPI 自带，命中不扣额度）+ 本地 TTL 1h（挡住重复请求） |
| 高德 POI | 本地 TTL 24h（景点数据变化慢），key = `(接口, 归一化参数)` |
| 高德 Direction | 本地 TTL 15min（受路况影响） |
| 重试 | 5xx / 超时：指数退避 3 次（0.5s / 1.5s / 4s）；4xx 不重试；高德 `infocode=10002`（限流）走退避重试 |
| 熔断 | 单 provider 连续 5 次失败 → 打开 60s，期间直接走降级路径 |

### 9.4 可观测性

- 结构化日志：每条带 `trip_id`、`node`、`tool`、`duration_ms`、`cache_hit`、`quota_used`。
- 每次规划结束落一条汇总：各 provider 调用次数、总耗时、命中的降级路径。SerpAPI 是稀缺资源，必须能事后追账。
- LangGraph 事件流同时写日志与 SSE，便于复现用户看到的过程。

### 9.5 测试策略

| 层 | 内容 |
|----|------|
| 单元 | `geo`（坐标转换用已知点对校验）、`dates`（相对日期表，对齐 `flight-data-specification.md` §4.3）、打分函数、TSP/分天算法（给定固定点集断言确定性输出） |
| 契约 | 用真实响应快照（fixture）测每个 Provider 的解析器；字段缺失/为 null 时不得抛异常（`type=vacation rental` 没有 `hotel_class`，骑行 `road` 可能为 null） |
| E2E | 全 mock provider + `MemorySaver`，跑通 `intake → done` 全图，覆盖：无中断路径、三个中断点各自的 resume、中断超时自动默认、航班为空的兜底重试链 |

真实 API 的联调测试单独标记，默认跳过——每跑一次就烧 SerpAPI 额度。

---

## 10. 已知风险

| # | 风险 | 影响 | 缓解 |
|---|------|------|------|
| R1 | Google Flights 对**中国国内航线**的覆盖与价格不如国内平台（航司直销价、特价票常缺失） | 用户看到的价格偏高或方案不全 | 展示时标注"价格来自 Google Flights，仅供参考"；预留 `FlightProvider` 接口以便后续接国内数据源 |
| R2 | Google Hotels 在大陆中小城市房源稀疏 | 酒店环节空结果 | §5.2 的高德 POI 降级（有坐标无房价），主流程不阻断 |
| R3 | SerpAPI 免费额度 250/月 | 约 50~125 次规划就耗尽 | 缓存纪律 + `/health` 暴露余量 + 达阈值时返回 `QUOTA_EXCEEDED` 而不是静默失败 |
| R4 | `MemorySaver` + 多 worker 会导致 `/answer` 找不到 thread | 中断后无法恢复 | v1 锁 `--workers 1`；上线前换 Sqlite/Postgres checkpointer |
| R5 | 高德 POI 分页上限 200 条、`keywords` 单词限制 | 大城市景点召回不全 | 多关键字多次检索 + 按 `poi.id` 去重 |
| R6 | LLM 在 `summarize` 阶段编造时间/价格 | 用户被误导 | summarize 只接收裁剪后的确定性 JSON，prompt 硬约束"只准复述给定数字"；行程数据本身不经过 LLM |

---

## 11. 落地顺序建议

1. **地基**：`config` / `providers` / `core.geo` / `core.dates` / `models` + 单元测试（坐标转换和日期解析先测对，后面全靠它）
2. **Tool 层**：11 个 Tool + registry 装饰器 + 契约测试（用 docs 里的 JSON 示例当 fixture）
3. **单分支跑通**：`intake → resolve_city → attraction_search`，先把不花 SerpAPI 额度的高德链路打通
4. **Flight 子图**：ReAct + 两个中断点 + 兜底重试
5. **Hotel 分支**：搜索 + 重排 + 高德降级
6. **Route Planner**：分天/排序/距离矩阵/明细路线（纯算法，可离线测）
7. **编排合拢**：并行分支 + checkpointer + interrupt/resume
8. **API 层**：SSE 事件流 + `/answer` + 鉴权限流
9. **Summarize + 端到端演练**

每步都能独立验证，第 3 步之后就有可演示的东西了。
