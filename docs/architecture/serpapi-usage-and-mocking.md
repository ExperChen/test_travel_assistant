# SerpAPI 用在哪、格式是什么、怎么换成模拟调用

> 编写日期：2026-08-10 ·  **本文只做盘点与设计，不含代码改动**
>
> 目的：把真实 SerpAPI 调用替换成模拟调用之前，先把「用了什么、传什么、回什么」
> 全部摊开，避免改到一半才发现漏了某条路径。
>
> 相关：[系统架构](system-architecture.md) ·
> [Google Flights API](../flight-agent/serpapi-google-flights-api.md) ·
> [Google Hotels API](../hotel/serpapi-google-hotels-api.md)

---

## 0. 一句话结论

**所有 SerpAPI 调用都收口在一个方法上**——[`SerpApiClient.search()`](../../app/providers/serpapi_client.py#L102)。
上层没有任何地方直接发 HTTP。这意味着切换到模拟调用**只需要替换这一个类**，
业务代码一行不用动。

而且替换的接缝已经现成：

| 接缝 | 位置 | 现状 |
|------|------|------|
| 模块级可替换单例 | [`registry.serpapi_client()`](../../app/tools/registry.py#L149) | 测试已在用 `override_clients()` |
| 每个 Tool 的 `client=` 形参 | `flights_search(..., client=None)` | 测试已在用 |
| respx 传输层拦截 | [`app/tests/e2e/_mocks.py`](../../app/tests/e2e/_mocks.py) | 733 个测试已全部跑在假响应上 |

**换句话说：模拟调用的基础设施已经存在，只是目前只在测试里用。**
要做的是把它提升成一个可以在开发/演示环境里长期开着的运行模式。

---

## 1. 用在哪：四个引擎，六个调用点

SerpAPI 只被两个 Tool 模块使用，共 4 个 engine：

| Engine | Tool 函数 | 文件 |
|--------|-----------|------|
| `google_flights_autocomplete` | `flights_autocomplete` | [app/tools/serpapi_flights.py](../../app/tools/serpapi_flights.py) |
| `google_flights` | `flights_search` | 同上 |
| `google_hotels` | `hotels_search` | [app/tools/serpapi_hotels.py](../../app/tools/serpapi_hotels.py) |
| `google_hotels_autocomplete` | `hotels_autocomplete` | 同上 ⚠️ **当前无人调用** |

### 调用链

```
graph/nodes/flight.py::flight_departure ─┐
graph/nodes/flight.py::flight_arrival   ─┴→ agents/flight_agent.py::resolve_airports
                                              └→ flights_autocomplete   [engine=google_flights_autocomplete]

graph/nodes/flight.py::flight_search ──→ agents/flight_agent.py::search_with_fallback
                                              └→ flights_search         [engine=google_flights]
                                         agents/flight_agent.py::fetch_return_departure
                                              └→ flights_search(departure_token=…)

graph/nodes/hotel.py::hotel_search ────→ agents/hotel_agent.py::search_hotels
                                              └→ hotels_search          [engine=google_hotels]
```

另有 [`scripts/debug_flights.py`](../../scripts/debug_flights.py) 直接调 `flights_search`（联调脚本，不在服务路径上）。

### 每次规划烧多少次额度

| 调用 | 次数 | 触发条件 |
|------|------|---------|
| `flights_autocomplete`（出发地） | 0 或 1 | 用户填的是 IATA 三字码就跳过（`looks_like_iata`） |
| `flights_autocomplete`（目的地） | 0 或 1 | 同上 |
| `flights_search`（去程） | 1 ~ 4 | 空结果时按「放宽舱位 → 换到达机场 → 换出发机场」重试，上限 `MAX_SEARCH_ATTEMPTS=4` |
| `flights_search`（返程，带 token） | 0 或 1 | 去程选定后必查一次；失败退回保守时刻 |
| `hotels_search` | 1 | 每次规划一次 |

**典型 2~5 次，最坏 7 次。** 免费额度 250 次/月 —— 这就是整个项目围绕配额做设计的原因，
也是要做模拟调用的直接动机。

> ⚠️ **中断重放会放大次数。** LangGraph resume 时会**从头重放整个节点**，
> 航班分支因此可能重复发起搜索。目前靠 [TTL 缓存](../../app/core/cache.py)吸收
> （`cache_ttl_serpapi_s=3600`，命中记 `quota.cache_hits` 不记 `quota.serpapi`）。
> 做模拟层时要注意**别把这层缓存一起绕过去**，否则重放行为会和线上不一致。

---

## 2. 统一的请求出口

所有请求都长这样（[`SerpApiClient.search`](../../app/providers/serpapi_client.py#L102)）：

```
GET https://serpapi.com/search.json
    ?engine=<engine>
    &<engine 各自的参数>
    &api_key=<注入，不进缓存键>
    &output=json
```

客户端在这一层统一做了五件事，**模拟层必须决定每一件要不要保留**：

| 行为 | 说明 | 模拟时的建议 |
|------|------|-------------|
| TTL 缓存 | `make_key(f"serpapi:{engine}", params)`，key 不含 api_key | **保留**——否则重放行为不一致 |
| 配额计数 | `record_call("serpapi", cached=…)` | **保留**——报表要能反映"如果是真调用会烧多少" |
| 重试 | 5xx/超时/429 退避 3 次 | 可关，模拟不会失败 |
| 熔断 | 连续 5 次失败开 60s | 可关 |
| 错误归一 | 一律抛 `AppError` 子类 | **保留**——要能模拟失败路径 |

### 必须知道的两个错误约定

1. **SerpAPI 经常用 HTTP 200 + `error` 字段报错**，见 `_raise_on_payload_error()`。
   模拟层要能构造这种响应，否则这条分支永远测不到。
2. **额度耗尽不触发熔断**——它不是 provider 故障。判据是响应里含
   `run out of searches` / `ran out of searches` / `exceeded your` / `plan limit`。

---

## 3. 逐引擎的请求与响应格式

### 3.1 `google_flights_autocomplete`

**请求**（[serpapi_flights.py:77](../../app/tools/serpapi_flights.py#L77)）

```jsonc
{
  "engine": "google_flights_autocomplete",
  "q": "北京",          // 城市名/机场名的部分或全部
  "hl": "zh-CN"        // ⚠️ 必传！不传时中文城市名一律返回空
}
```

> ⚠️ `hl` 是实测踩出来的：不加它，「成都」「杭州」「北京」全部返回空数组，
> 加上立刻返回 TFU/CTU。目的地限中国大陆意味着用户几乎必然中文输入，
> 漏了这个参数整条链路会在第一步就死。

**响应**

```jsonc
{
  "search_metadata": { "id": "…", "status": "Success" },
  "suggestions": [
    {
      "type": "City",
      "name": "北京",
      "id": "/m/01914",
      "description": "…",
      "airports": [
        { "name": "北京首都国际机场", "id": "PEK", "city": "北京", "distance": "25 km" },
        { "name": "北京大兴国际机场", "id": "PKX", "city": "北京", "distance": "46 km" }
      ]
    }
  ]
}
```

**解析成** `list[CitySuggestion]`（[models/flight.py](../../app/models/flight.py#L67)）。
单条脏数据被跳过而不是整体失败。

**下游怎么用**：`flatten_airports()` 取**第一个带机场的建议**作为锚点城市，
并过滤掉混在里面的地面交通枢纽（`ZAQ` Nuremberg Hbf 之类，判据见 `_RAIL_TERMS`）。

---

### 3.2 `google_flights`

**请求**（[models/flight.py::to_serpapi](../../app/models/flight.py#L221)）

```jsonc
{
  "engine": "google_flights",
  "departure_id": "PEK",           // IATA
  "arrival_id": "HGH",
  "outbound_date": "2026-09-05",   // YYYY-MM-DD
  "return_date": "2026-09-10",     // 仅 type=1 时出现
  "type": 1,                       // ⚠️ 1=往返，2=单程
  "adults": 1,
  "children": 0,                   // 仅 >0 时出现
  "travel_class": 1,               // 1/2/3/4，仅指定时出现
  "currency": "CNY",
  "hl": "zh-CN",
  "gl": "",                        // ⚠️ 默认**不传**，见下
  "departure_token": "…"           // 仅查返程时出现
}
```

> ⚠️ **`type` 的取值和官方文档写反了。** `serpapi-google-flights-api.md` §4 写
> 「1=单程，2=往返」，照抄会得到 HTTP 400。已实测确认：**1=往返、2=单程**。
>
> ⚠️ **`gl` 默认留空是刻意的，不是遗漏。** 实测 PEK→CTU 同一时刻对照：
> 留空返回 3 条 ¥4685 直飞；`gl=cn` 把两段中转排到首位（¥4735）、直飞报价反而
> 涨到 ¥6160、还有条目价格为 null。Google 在中国销售地的机票库存覆盖本来就差。
> 依据见 [`settings.serpapi_flights_gl`](../../app/config.py) 的注释。

`travel_class` 映射：`economy=1` / `premium_economy=2` / `business=3` / `first=4`。

**响应**

```jsonc
{
  "search_metadata": { "status": "Success", "total_time_taken": 2.6 },
  "best_flights": [
    {
      "flights": [
        {
          "departure_airport": { "name": "首都国际机场", "id": "PEK", "time": "2026-09-05 08:00" },
          "arrival_airport":   { "name": "萧山国际机场", "id": "HGH", "time": "2026-09-05 10:30" },
          "duration": 150,                    // 本段分钟数
          "airplane": "Boeing 737",
          "airline": "中国国航",
          "airline_logo": "https://…",
          "travel_class": "Economy",
          "flight_number": "CA100",
          "legroom": "31 in",
          "overnight": false,
          "often_delayed_by_over_30_min": false,
          "extensions": ["…"]
        }
      ],
      "layovers": [ { "duration": 205, "name": "…", "id": "SFO", "overnight": false } ],
      "total_duration": 150,                  // 全程分钟数
      "carbon_emissions": { "this_flight": 902000, "typical_for_this_route": 950000, "difference_percent": -5 },
      "price": 1200,                          // ⚠️ 可能为 null = "价格暂无"
      "type": "Round trip",
      "airline_logo": "https://…",
      "departure_token": "abc123=="           // 查返程要用它
    }
  ],
  "other_flights": [ /* 同构 */ ]
}
```

**解析成** `FlightSearchResults`，`best_flights`/`other_flights` **各截前 3 条**
（`MAX_CANDIDATES_PER_GROUP=3`）。

#### 三个必须在模拟数据里如实还原的行为

1. **往返搜索的 `best_flights` 里只有去程航段。** 要拿返程，必须带选定去程的
   `departure_token` **再查一次**，返回的才是配对的返程列表——**这是第二次额度消耗**。
   模拟层要支持"同一组参数 + 不同 token → 不同响应"。
2. **返程查询的航段方向是反的**（目的地→出发地）。代码里
   `_drop_off_route()` 会校验航线，但**带 token 时刻意跳过校验**，
   否则返程会被全部误杀。
3. **`price` 可能是 `null`**，表示"价格暂无"，不是 0。模拟数据里要有这种条目
   （契约测试 `test_null_price_survives` 覆盖了它）。

---

### 3.3 `google_hotels`

**请求**（[models/hotel.py::to_serpapi](../../app/models/hotel.py#L193)）

```jsonc
{
  "engine": "google_hotels",
  "q": "杭州 西湖",                  // 列表模式；单店模式可省
  "property_token": "…",            // 单店模式必填
  "check_in_date": "2026-09-05",
  "check_out_date": "2026-09-10",
  "adults": 2,
  "children": 1,                    // 仅 >0 时出现
  "children_ages": "8",             // 逗号分隔，长度必须等于 children
  "sort_by": 3,                     // 3=最低价 8=最高评分 13=评论最多；不传=相关度
  "min_price": 200,
  "max_price": 600,
  "rating": 8,                      // 7=3.5+ 8=4.0+ 9=4.5+
  "hotel_class": "4,5",             // 逗号分隔；vacation_rentals 模式下不发
  "free_cancellation": "true",      // 同上
  "vacation_rentals": "true",       // 民宿模式，会让上面两个筛选失效
  "next_page_token": "…",
  "gl": "cn",
  "hl": "zh-CN",
  "currency": "CNY"
}
```

> 注意 `gl`/`hl`/`currency` 在酒店链路上**跟随** `default_*`（和航班不同）——
> 酒店的 `gl=cn` 是正常的。

**响应**

```jsonc
{
  "search_metadata": { "status": "Success" },
  "ads": [                          // 广告位：价格胶囊卡片
    {
      "name": "广告酒店",
      "source": "Booking.com",
      "property_token": "tok-ad-1",
      "gps_coordinates": { "latitude": 30.30, "longitude": 120.30 },
      "overall_rating": 4.5,
      "reviews": 120,
      "price": "¥380",              // ⚠️ ads 只有单晚价
      "extracted_price": 380,       //    没有 total_rate
      "amenities": ["泳池"],
      "hotel_class": "4-star hotel" // 或 extracted_hotel_class: 4
    }
  ],
  "properties": [                   // organic 结果
    {
      "type": "hotel",             // 或 "vacation rental"
      "name": "酒店0",
      "property_token": "tok-hotel-0",
      "gps_coordinates": { "latitude": 30.24, "longitude": 120.15 },
      "rate_per_night": { "lowest": "¥400", "extracted_lowest": 400 },
      "total_rate":     { "lowest": "¥1200", "extracted_lowest": 1200,
                          "before_taxes_fees": "¥1100", "extracted_before_taxes_fees": 1100 },
      "extracted_hotel_class": 4,
      "overall_rating": 4.8,
      "reviews": 500,
      "amenities": ["免费 Wi-Fi", "健身房"],
      "location_rating": 4.2,
      "thumbnail": "https://…",
      "deal_description": "…",
      "link": "https://…",
      "nearby_places": [
        { "name": "西湖", "transportations": [ { "type": "Walking", "duration": "9分钟" } ] }
      ]
    }
  ]
}
```

**解析成** `list[HotelCandidate]`，`ads` 在前 `properties` 在后，**合并后截前 10 条**
（`MAX_HOTEL_RESULTS=10`）。设施截前 6（`MAX_AMENITIES`），周边地标截前 3（`MAX_NEARBY`）。

#### 四个容易在模拟数据里做错的地方

1. **`gps_coordinates` 是 WGS-84**，不是 GCJ-02。进高德前必须转换，
   `GeoPoint.from_google()` 已封装。**模拟数据也要给 WGS-84 值**，
   否则坐标转换那条路径就被绕过了，线上一接真接口会出现 300~600m 系统性偏移。
2. **`properties[]` 里根本没有 address 字段。** Google Hotels 不返回门牌号地址，
   位置信息只有 `nearby_places`。项目里的酒店地址是**事后用高德逆地理编码补的**。
3. **`ads` 只有单晚价、`properties` 才有 `total_rate`。** 混排时若只显示一个字段，
   「总价 ¥301」会看着比「¥190/晚」贵，而前者每晚其实才 ¥100。`price_text()` 处理了这点。
4. **`max_price` 只作用于 organic 结果，`ads[]` 不受约束。** 实测成都 `--budget 500`
   依然返回 ¥725/晚 的广告位。`drop_over_budget()` 在本地再筛一遍——
   **模拟数据应当保留这个"不听话"的行为**，否则本地筛选逻辑就没被验证到。

也要覆盖：`hotel_class` 有 `extracted_hotel_class`（int）和 `"5-star hotel"`（str）
两种形态；民宿条目可能**完全没有** hotel_class。

---

### 3.4 `google_hotels_autocomplete` ⚠️ 目前无人调用

`hotels_autocomplete()` 已实现并注册为 Tool，但**服务路径上没有任何地方调用它**
（酒店查询直接用 `build_query()` 拼出来的关键词）。

**请求**

```jsonc
{ "engine": "google_hotels_autocomplete", "q": "西湖", "gl": "cn", "hl": "zh-CN", "currency": "CNY" }
```

**响应** `suggestions[]`，每条含 `value` / `type` / `location` /
`autocomplete_suggestion`（选中后交给 `google_hotels` 的 `q`）/
`property_token`（有 = 具体门店）/ `kgmid`（有 = 品牌）。

它**只被契约测试用到**（[test_tool_parsers.py:312](../../app/tests/contract/test_tool_parsers.py#L312)），
且已有真实响应快照 `app/tests/fixtures/hotels_autocomplete.json`。

> **做模拟层时的决策点**：这个 engine 要不要一起模拟？
> 建议**要**——它已经是 Tool 注册表的一员，将来 ReAct agent 若要用到，
> 模拟层没覆盖会成为一个隐藏的真实调用漏点。而且快照现成，几乎零成本。

---

## 4. 已有的模拟基础设施

### 4.1 ⭐ 四个引擎的**真实响应快照已经在库里了**

[`app/tests/fixtures/`](../../app/tests/fixtures/) 下已有从真实接口抓取的完整响应，
四个 engine 全覆盖：

| 文件 | 顶层键 | 备注 |
|------|--------|------|
| `flights_autocomplete.json` | `search_metadata` / `suggestions` | |
| `flights_search.json` | `search_metadata` / `best_flights` / `other_flights` | |
| `hotels_autocomplete.json` | `search_metadata` / `search_parameters` / `suggestions` | |
| `hotels_search.json` | + `search_information` / `brands` / `ads` / `properties` / `serpapi_pagination` | ads 2 条 + properties 2 条 |

它们**确实是真实抓取**而非手写——含有 `search_parameters`、`brands`、
`serpapi_pagination`、`check_in_time`、`excluded_amenities`、`prices` 这些
`_mocks.py` 从不构造的字段。**且已确认不含 api_key。**

契约测试 [`test_tool_parsers.py`](../../app/tests/contract/test_tool_parsers.py)
就是拿它们喂解析器的（`load_fixture()` fixture）。

> **这把方案 B（录制回放）的成本砍掉一大半**：种子数据已经有了，
> 缺的只是"按参数选哪一份"的调度逻辑，以及更多样本（不同城市、空结果、报错）。

### 4.2 手写构造器

[`app/tests/e2e/_mocks.py`](../../app/tests/e2e/_mocks.py) 能按参数生成响应：

| 函数 | 产出 |
|------|------|
| `autocomplete_payload(city, airports)` | flights autocomplete |
| `outbound_payload(date, count, dep_id, arr_id)` | 去程 |
| `return_payload(date, hour, dep_id, arr_id)` | 返程（方向相反） |
| `empty_flights_payload()` | 空结果，测兜底链 |
| `hotels_payload(count, with_ads)` | ads + properties |
| `empty_hotels_payload()` | 空结果，测高德降级 |

拦截方式是 **respx 在传输层拦 httpx**，按 query 参数分发：

```python
# ⚠️ 注册顺序有讲究：带 departure_token 的必须先注册，
#    否则会被更宽泛的 engine=google_flights 抢先匹配，返程就变成了去程
respx.get(SERP_URL, params__contains={"engine": "google_flights_autocomplete"}).mock(…)
respx.get(SERP_URL, params__contains={"departure_token": OUTBOUND_TOKEN}).mock(…)
respx.get(SERP_URL, params__contains={"engine": "google_flights"}).mock(…)
```

**局限**：respx 是测试库，依赖 `httpx` 的传输钩子，不适合作为长期运行的服务模式。

---

## 5. 三种可选方案

| 方案 | 做法 | 优点 | 缺点 |
|------|------|------|------|
| **A. 假客户端** | 写一个 `FakeSerpApiClient`，与 `SerpApiClient` 同接口，从 fixture 目录读 JSON；`SERPAPI_MOCK=true` 时由 `registry.serpapi_client()` 返回它 | 改动最小（只碰 registry 一处）；不依赖测试库；可长期开着 | 需要自己维护 fixture 选择逻辑 |
| **B. 录制回放** | 真实调用时把 `(engine, 归一化参数) → 响应` 落盘；之后优先命中录像 | 数据真实；一次真调用长期复用；能发现上游字段变化 | 要处理录像失效、参数归一化；录像里可能混进 api_key（需清洗） |
| **C. 本地假服务** | 起一个小 HTTP 服务，把 `SERPAPI_BASE_URL` 指过去 | 完全不动应用代码；可跨语言复用 | 多一个进程要管；与缓存/熔断的交互更难验证 |

**推荐 A + B 组合**：以 A 为骨架（`FakeSerpApiClient`），数据来源优先读录像（B），
录像缺失时回落到手写构造器。理由：

- `SerpApiClient` 的公开接口只有 `search()` 和 `aclose()`，替换成本极低；
- `registry.override_clients()` 这个接缝已经存在且被测试验证过；
- **§4.1 的四份真实快照已经是现成的种子录像**，B 的启动成本很低；
- 录像能覆盖手写数据想不到的真实脏数据（价格 null、缺 hotel_class、
  中英文混排的 `nearby_places`、`prices[]` 这类没人解析但真实存在的字段）。

### 建议的落点

```
app/providers/serpapi_fake.py       # FakeSerpApiClient，与 SerpApiClient 同接口
fixtures/serpapi/
  ├── recorded/                     # 录制的真实响应（key = engine + 归一化参数）
  │     └── (可直接把 app/tests/fixtures/*.json 那四份挪/复制过来当种子)
  └── handcrafted/                  # 手写的边界用例
        ├── flights_empty.json
        ├── flights_null_price.json
        ├── hotels_ads_over_budget.json
        └── error_quota_exhausted.json
```

> ⚠️ 若决定把 `app/tests/fixtures/*.json` **移动**过去，注意契约测试的
> `load_fixture()` 指向 `app/tests/fixtures/`——移动会连带改测试。
> 想零风险的话先**复制**，等模拟层稳定了再合并。

配置项（沿用现有风格）：

```dotenv
# 用模拟响应替代真实 SerpAPI 调用。不烧额度，适合开发与演示。
SERPAPI_MOCK=false
SERPAPI_FIXTURE_DIR=fixtures/serpapi
# 录像缺失时：error=报错（暴露覆盖缺口） / handcrafted=回落手写 / passthrough=真调用
SERPAPI_MOCK_MISS=handcrafted
```

---

## 6. 改之前必须想清楚的六件事

1. **缓存与配额计数保不保留？** 建议**保留**。模拟模式下仍然记
   `record_call("serpapi")`，这样"这次规划会烧几次额度"这个数字在模拟环境里依然准确——
   否则模拟环境测不出配额问题，而配额是本项目的第一约束。

2. **失败路径怎么模拟？** 至少要能造出：空结果、HTTP 200+`error` 字段、429、
   额度耗尽文案、超时。**这些正是真实环境最常见的分支**，模拟层若只会返回成功响应，
   等于把兜底链（`search_with_fallback` 的 4 次重试、酒店降级到高德）全部旁路掉了。

3. **`departure_token` 的双查询怎么表达？** 同一组参数带不带 token 必须返回不同响应。
   fixture 的 key 里必须包含 token，否则返程会拿到去程数据——
   `_mocks.py` 里那条"必须先注册 token 路由"的注释就是踩过这个坑。

4. **参数归一化到什么粒度？** 日期一变就 miss 会让录像几乎无法复用
   （用户总在查未来日期）。建议 key 里**忽略具体日期**，只保留
   `engine + departure_id + arrival_id + type`，把日期作为响应生成时的模板变量替换进去
   —— `_mocks.py` 的 `outbound_payload(outbound_date)` 已经是这个思路。

5. **录像里的密钥要清洗。** `search_metadata` 里可能带回显信息；
   录制时应剥掉 `api_key`（缓存键本来就不含它，录像也不该有）。

6. **`hotels_autocomplete` 要不要一起做？** 它现在没人调用，但已经在 Tool 注册表里。
   不覆盖 = 留一个隐藏的真实调用漏点。建议一起做。

---

## 7. 一个现成的验收标准

改完之后应当满足：

```bash
# 断网 / 不配 SERPAPI_KEY 也能跑通完整规划
SERPAPI_MOCK=true SERPAPI_KEY= uv run python scripts/demo_trip.py

# 且配额报表如实反映"若是真调用会烧多少次"
# 期望：quota.serpapi 仍然是 2~5，不是 0
```

以及：现有 733 个测试**不受影响**——它们走的是 respx 传输层拦截，
与新的 `FakeSerpApiClient` 是两条独立的路径，不该互相干扰。
如果加了模拟层导致测试需要改，说明抽象位置选错了。
