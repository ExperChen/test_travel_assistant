# 酒店数据源契约 —— 换供应商时要读的那一份

> 编写日期：2026-08-10
>
> **读者**：要把 Google Hotels（SerpAPI）换成别的酒店数据源的人。
>
> 本文回答的不是"SerpAPI 怎么调"（那在
> [serpapi-google-hotels-api.md](serpapi-google-hotels-api.md) 与
> [../architecture/serpapi-usage-and-mocking.md](../architecture/serpapi-usage-and-mocking.md)），
> 而是：**换掉数据源时，新的源必须提供什么、可以不提供什么、有哪些坑是系统已经
> 替你踩过的。**

---

## 0. 先说结论

换酒店源要动的代码**只有一个函数**：

```
app/agents/hotel_agent.py::search_hotels()   ← 唯一的接入点
```

它上面的 `hotel_search` 节点、重排、追问、行程编排全都只认
`list[HotelCandidate]`，不关心数据从哪来。

而且系统里**已经跑着两个酒店数据源**——这不是设想，是现状：

| 源 | 触发时机 | 提供 | 缺什么 |
|----|---------|------|--------|
| Google Hotels（SerpAPI） | 主路径 | 房价、评分、星级、坐标、设施 | **地址**（不返回门牌号） |
| 高德 POI「住宿服务」 | Google 无结果时降级 | 坐标、评分、地址 | **房价**（标 `price_unavailable`） |

**这两个源的交集，就是真正的最小契约。** 下面的字段分级不是设计出来的，
是从"高德降级路径能跑通"这个既成事实里读出来的。

---

## 1. 内部契约：`HotelCandidate`

定义在 [`app/models/hotel.py`](../../app/models/hotel.py#L110)。
下表的"被消费处数"是对 `app/` 下非测试代码的实测统计，
用来区分**真需要**和**只是存着**。

### 1.1 必须提供（缺了功能就残）

| 字段 | 类型 | 消费点 | 缺了会怎样 |
|------|------|--------|-----------|
| `name` | `str` | 77 处 | 无法展示、无法去重、追问选项无文案 |
| `location` | `GeoPoint` | 11 处 | **整条路径规划链断掉**——通勤重排、行程编排、地图展示全依赖它 |

`location` 是**唯一的硬依赖**。没有坐标的候选会被 `attach_commute()` 跳过、
被 `route_planner` 判为"没有选定的酒店"直接失败。

> ⚠️ **坐标系必须如实标注。** `GeoPoint` 强制携带 `crs`：
> Google 给 WGS-84，高德给 GCJ-02。标错不会报错，只会静默产生 300~600m 偏移——
> 足以把酒店定位到马路对面的另一个街区并给出错误路线。
> 新源接入时**第一件事**就是查清它的坐标系。

### 1.2 强烈建议提供（缺了质量明显下降）

| 字段 | 类型 | 消费点 | 缺了会怎样 |
|------|------|--------|-----------|
| `total_rate` / `rate_per_night` | `Rate` | 各 10+ 处 | 重排的价格维度退化成中位分；费用明细缺失；用户看到"价格暂无" |
| `overall_rating` | `float\|None` | 3 处 | 重排的评分维度退化成中位分 |
| `address` | `str` | 2 处 | 系统会**自动用高德逆地理编码补**（1 次额度覆盖全部候选），所以可以不给 |
| `hotel_class` | `int\|None` | 4 处 | 星级筛选失效，追问选项少一个信息维度 |

价格的两个字段有**明确的语义分工**，不能混：

```
rate_per_night  = 起价（通常税前）
total_rate      = 整段入住实付总价（含税含费）
```

系统优先用 `total_rate`；只有它缺失时才用 `nightly_price × 晚数` 兜底
（见 `_effective_price()`）。**新源如果只能给单晚价，就只填 `rate_per_night`，
不要自己乘出一个假的 `total_rate`**——那会让费用明细系统性偏低。

### 1.3 可以不提供（纯展示或当前无人消费）

| 字段 | 消费点 | 说明 |
|------|--------|------|
| `reviews` | 0 | 评论数，仅展示 |
| `nearby_places` | 0 | Google 特有；换源后可留空 |
| `location_rating` | 0 | Google 特有的地段评分 |
| `amenities` / `thumbnail` / `link` / `deal_description` / `source` | 0 | 展示用 |
| `property_token` | 1 | 仅用于**去重键**，`_dedupe()` 会退回用 `name` |

> 这些字段留空**不会**触发任何降级或告警。但注意：`amenities`、`thumbnail`
> 一旦前端接了就会变成事实契约，删之前先确认。

### 1.4 由系统自己填，数据源**不要**碰

| 字段 | 谁填 |
|------|------|
| `commute_to_centroid_min` | `attach_commute()`，高德 `distance_batch` |
| `score` | `rerank_hotels()` |
| `address`（当源没给时） | `attach_addresses()`，高德 `regeo_batch` |
| `is_ad` | 由源的广告位标记决定，但**不过滤，只打标签** |
| `price_unavailable` | 源确实没有房价时置 `true` |

---

## 2. 适配器要实现的接口

替换 [`search_hotels()`](../../app/agents/hotel_agent.py#L141) 即可，签名保持不变：

```python
async def search_hotels(
    request: TripRequest,      # 用户需求（人数/儿童年龄/预算/星级/日期）
    query: str,                # build_query() 的产物，见 §3
    *,
    gl: str,                   # 销售地，如 "cn"
    hl: str,                   # 语言，如 "zh-CN"
    currency: str,             # 币种，如 "CNY"
    client=None,               # 可注入的客户端，测试与模拟层靠它
) -> list[HotelCandidate]:
    ...
```

**四条硬性约定**：

1. **只抛 `AppError` 子类。** 上层 `hotel_search` 节点靠 `except AppError` 转成
   结构化错误；漏出 `httpx.TimeoutException` 之类会变成 500。
2. **空结果返回 `[]`，不要抛异常。** 空是正常情况，会触发高德降级那条路径。
3. **自己做去重。** 现有实现用 `_dedupe()` 按 `property_token or name` 合并，
   并把重复项的 `total_rate` 补到主条目上。
4. **保留 `client=` 形参。** 它是测试注入与 `SERPAPI_MOCK` 的接缝，去掉会让
   799 个测试里的酒店部分全部失效。

### 建议的落地形态

不要直接改 `search_hotels()` 的内部，而是做成可切换的 provider：

```
app/providers/hotels/
├── base.py          # HotelProvider 协议：async def search(...) -> list[HotelCandidate]
├── serpapi.py       # 现有实现搬过来
├── <新源>.py
└── amap.py          # 降级源（现在在 hotel_agent.fallback_to_amap 里）
```

`search_hotels()` 退化成"选 provider + 调用 + 去重"，
选择依据放 `settings.hotel_provider`。这样新旧源可以并行跑对比，
也便于按城市分流（大城市用商业源、小城市用地图源）。

---

## 3. 请求侧：系统会给数据源什么

### 3.1 查询词 `query`

由 [`build_query()`](../../app/agents/hotel_agent.py#L108) 生成，两种形态：

```
"成都市天府广场附近酒店"    ← 景点集中在某商圈时
"成都市酒店"               ← 景点分散时
```

商圈来自景点的 `business_area` 众数。**注意城市名带「市」**（高德口径）。

> 新源如果接受结构化参数（城市 ID + 经纬度 + 半径）而不是自然语言查询词，
> 那比现在**更好**——`build_query()` 拼字符串本来就是为了迁就 Google 的
> 自然语言检索。这时可以直接用 `state["dest_city"]`（含 adcode/citycode/中心坐标）
> 和 `attractions.centroid`，精度更高。

### 3.2 其余入参

| 来自 `TripRequest` | 语义 | 注意 |
|---|---|---|
| `outbound_date` / `return_date` | 入住 / 离店 | **日期永远必填**，哪怕单店查询 |
| `adults` | 成人数 | |
| `children` + `children_ages` | 儿童数与年龄 | **长度必须相等**，年龄 1~17，1 岁以下填 1 |
| `budget_per_night` | 每晚上限 | 未填时**不要传上限**——瞎设是空结果最常见的原因 |
| `hotel_class` | 星级筛选 `[2,3,4,5]` | |

---

## 4. 五个已经踩过的坑

换源时逐条确认新源的行为，**不要假设它和 Google 一样**。

### 4.1 价格筛选可能只约束一部分结果

Google Hotels 的 `max_price` **只作用于 organic，`ads[]` 不受约束**——
实测成都 `--budget 500` 依然返回 ¥725/晚 的广告位，还因为离景点近排到第一。

系统因此在本地又筛了一遍（`drop_over_budget()`）。这层**保留着**：
新源即使老实听话，多筛一遍也没有代价；而它一旦不听话，用户说的预算就被当真了。

### 4.2 全部被筛光时不能返回空

`drop_over_budget()` 在筛光时**退回原样并告警**——给一份超预算的候选，
好过给一个空列表。空列表会让整次规划失败，而用户其实只是预算定低了。

### 4.3 "没标价"不等于"免费"

价格缺失的候选一律 `price_unavailable=True`，展示成"价格暂无"。
`price_text()` 绝不显示 `¥0`。新源返回 0 价时要判断是真免费还是没数据。

### 4.4 广告位要打标签但不能过滤

`is_ad` 只打标签。但 `pick_options()` 会**保证追问选项里有非广告位**——
实测成都 8 家候选 6 家是广告，只列广告等于没得选。

新源若没有"广告"概念，`is_ad` 全部留 `False` 即可，这段逻辑会自动空转。

### 4.5 单晚价与总价混排会误导

ads 只有单晚价、organic 才有总价，直接并排会让
「总价 ¥301」看着比「¥190/晚」贵，而前者每晚其实才 ¥100。
`price_text()` 统一折算成「¥X/晚 · 共 ¥Y」两个都给。

---

## 5. 换源检查清单

- [ ] 坐标系确认（WGS-84 / GCJ-02 / BD-09），并在 `GeoPoint` 上如实标注
- [ ] `location` 一定有值——没有坐标的候选等于不可用
- [ ] `total_rate` 与 `rate_per_night` 的税费口径分清，别自己乘
- [ ] 空结果返回 `[]` 而不是抛异常（降级路径靠它）
- [ ] 所有异常归一成 `AppError` 子类
- [ ] 保留 `client=` 形参
- [ ] 价格筛选是否覆盖全部结果？不确定就依赖本地二次筛选
- [ ] 是否有分页/配额限制？现有实现取前 10 条（`MAX_HOTEL_RESULTS`）
- [ ] 补一个模拟实现（对标 [`app/providers/mock/hotels.py`](../../app/providers/mock/hotels.py)）
- [ ] 契约测试：拿新源的**真实响应快照**喂解析器，别只测自己造的数据

---

## 6. 候选数据源的取舍

按本项目的实际需求（目的地限中国大陆、需要坐标、需要真实房价）：

| 源 | 房价 | 坐标 | 中国大陆覆盖 | 主要障碍 |
|----|------|------|------------|---------|
| **Google Hotels（现状）** | ✅ | ✅ WGS-84 | ⚠️ 中小城市稀疏 | 免费额度 250 次/月 |
| **高德 POI（现状降级）** | ❌ | ✅ GCJ-02 | ✅ 最好 | 没有房价，只能兜底 |
| 携程 / 美团开放平台 | ✅ | ✅ | ✅ | 需要商务合作与资质 |
| Booking / Agoda 联盟 | ✅ | ✅ WGS-84 | ⚠️ 一般 | 联盟准入；大陆库存不如本土 |
| Amadeus / Sabre (GDS) | ✅ | ✅ | ⚠️ 偏国际连锁 | 面向 B 端，接入重 |

> **最可能的组合是"商业源 + 高德补位"**：商业源给房价，高德给坐标与地址。
> 这正是现在的形态，只是把 Google 换掉——所以 `fallback_to_amap()` 那条路径
> 不但要保留，还会变得更重要。

---

## 7. 落地顺序建议

1. **先抽 provider 接口**（`app/providers/hotels/base.py`），把现有 SerpAPI 实现原样搬进去，跑通 799 个测试——这一步不引入任何行为变化
2. **加 `settings.hotel_provider` 开关**，默认仍是 serpapi
3. **接新源 + 写它的模拟实现**，两者一起做（模拟实现是新源的第一个"契约测试"）
4. **并行对比**：同一批查询两个源都跑，比候选重合度、价格差、坐标偏移
5. **按城市分流**再全量切换

每一步都能单独上线，第 1 步尤其值得先做——它把"换源"从一次大手术拆成了可回滚的小改动。

---

## 附：相关文件速查

| 文件 | 作用 |
|------|------|
| [`app/agents/hotel_agent.py`](../../app/agents/hotel_agent.py) | **唯一接入点** `search_hotels()`、重排、降级 |
| [`app/models/hotel.py`](../../app/models/hotel.py) | `HotelCandidate` 契约 |
| [`app/tools/serpapi_hotels.py`](../../app/tools/serpapi_hotels.py) | 现有 SerpAPI 实现（解析 + 裁剪） |
| [`app/graph/nodes/hotel.py`](../../app/graph/nodes/hotel.py) | 图节点：调用、追问、选定 |
| [`app/providers/mock/hotels.py`](../../app/providers/mock/hotels.py) | 模拟实现，可作为新源模拟层的模板 |
| [`app/tests/fixtures/hotels_search.json`](../../app/tests/fixtures/hotels_search.json) | 真实响应快照 |
