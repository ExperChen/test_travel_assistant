# 酒店 API 入参变量表

> 编写日期：2026-08-10 · 格式对齐 SerpAPI `engine=google_hotels`
>
> [爬来的官方参数全表](serpapi-google-hotels-api.md#3-请求参数全表)讲的是"这个接口
> **能**传什么"；本文讲的是**我们这套系统实际传什么、值从哪来、被什么规则约束**。
>
> 换数据源时，本文是"输入侧"的映射依据；输出侧见
> [酒店数据源契约](hotel-provider-contract.md)。

---

## 0. 变量的三层流转

一个酒店查询的参数要穿过三层，每层职责不同：

```
① TripRequest              用户/前端给的原始需求
        ↓  search_hotels()  —— 挑出与酒店相关的字段
② HotelSearchParams        内部模型，**本地校验在这一层**
        ↓  to_serpapi()     —— 条件发送，空值不发
③ HTTP query               真正发出去的 query string
```

关键在第②→③层：**`to_serpapi()` 只发有值的参数**。这不是省字节，
而是因为传空值和不传在 Google 这里语义不同——见 §4。

---

## 1. 变量总表

### 1.1 必填（缺一个就发不出去）

| SerpAPI 参数 | 类型 | 我们的来源 | 说明 |
|---|---|---|---|
| `engine` | str | 常量 | 固定 `google_hotels` |
| `check_in_date` | `YYYY-MM-DD` | `TripRequest.outbound_date` | 入住日 = 出发日 |
| `check_out_date` | `YYYY-MM-DD` | `TripRequest.return_date` | 离店日 = 返程日；**必须晚于入住日** |
| `q` **或** `property_token` | str | `build_query()` | 二选一，我们只用 `q` |
| `api_key` | str | `settings.serpapi_key` | 由 `SerpApiClient` 注入，**不进缓存键** |

> ⚠️ **日期永远必填，哪怕单店查询。** 这是 Google Hotels 与多数酒店 API 的
> 共同点——房价本身就是日期的函数，没有日期就没有价格。

### 1.2 我们会传的可选参数

| SerpAPI 参数 | 类型 | 来源 | 默认 | 备注 |
|---|---|---|---|---|
| `adults` | int | `TripRequest.adults` | 我们的默认 **1** | ⚠️ SerpAPI 自己的默认是 **2**，所以**必须显式发**，否则单人行程会按双人报价 |
| `children` | int | `TripRequest.children` | 0 | 仅 >0 时发 |
| `children_ages` | `"8,12"` | `TripRequest.children_ages` | — | 逗号串；**个数必须等于 `children`** |
| `max_price` | int | `TripRequest.budget_per_night` | 不传 | **每晚**上限，不是总价 |
| `hotel_class` | `"4,5"` | `TripRequest.hotel_class` | 不传 | 逗号串；`vacation_rentals` 模式下不发 |
| `gl` | str | `settings.default_gl` | `cn` | 销售地 |
| `hl` | str | `settings.default_hl` | `zh-CN` | 语言 |
| `currency` | str | `settings.default_currency` | `CNY` | 价格币种 |

> `gl` / `hl` / `currency` 三个必须**在一次会话内恒定**——它们由 `intake` 节点
> 写进 `LocaleCtx` 后全程只读。混用会让各处价格币种对不上。
>
> 注意与航班的区别：航班链路的 `gl` **刻意留空**（Google 在中国销售地的机票
> 库存覆盖差，实测会把两段中转排到直飞前面）；酒店这边 `gl=cn` 是正常的。

### 1.3 工具已支持、但当前从不传

`hotels_search()` 这个 Tool 接受它们，`search_hotels()` 却没有传——
**是有意留的扩展位，不是遗漏**：

| 参数 | 为什么现在不用 | 什么时候值得用 |
|---|---|---|
| `sort_by` | 我们自己重排（通勤权重 0.55 压倒价格与评分），上游排序会被覆盖 | 想省下本地重排时 |
| `min_price` | 用户只表达"预算上限"，没有下限语义 | 要过滤明显异常的低价时 |
| `rating` | 评分已进重排公式，硬筛会缩小候选池 | 候选过多时 |
| `free_cancellation` | 用户需求里没有这个字段 | `TripRequest` 加了退改偏好后 |
| `vacation_rentals` | 民宿模式会让星级等筛选全部失效 | 做"民宿专线"时 |
| `property_token` | 单店精准模式；我们只做列表检索 | 用户指定某家酒店时 |
| `next_page_token` | 取前 10 条足够（`MAX_HOTEL_RESULTS`） | 需要更多候选时 |

### 1.4 SerpAPI 支持、但我们连 Tool 都没接

`property_types`、`amenities`、`brands`、`special_offers`、`eco_certified`、
`bedrooms`、`bathrooms`、`no_cache`、`async`、`json_restrictor`。

> `no_cache` 是**刻意不接**的：SerpAPI 服务端 1 小时缓存命中**不扣额度**，
> 主动传 `no_cache=true` 等于白烧配额。免费额度只有 250 次/月。

---

## 2. 一个真实请求长什么样

输入：成都，2026-09-05 至 09-10，2 大 1 小（8 岁），预算 600/晚，四星或五星。

```json
{
  "engine": "google_hotels",
  "check_in_date": "2026-09-05",
  "check_out_date": "2026-09-10",
  "adults": 2,
  "gl": "cn",
  "hl": "zh-CN",
  "currency": "CNY",
  "q": "成都市天府广场附近酒店",
  "children": 1,
  "children_ages": "8",
  "max_price": 600,
  "hotel_class": "4,5"
}
```

对应 URL：

```
https://serpapi.com/search.json?engine=google_hotels
  &check_in_date=2026-09-05&check_out_date=2026-09-10
  &adults=2&children=1&children_ages=8
  &q=成都市天府广场附近酒店&max_price=600&hotel_class=4,5
  &gl=cn&hl=zh-CN&currency=CNY
  &api_key=***&output=json
```

同一需求若没填预算、没选星级、无儿童，发出的就只有 7 个参数——
`children` / `children_ages` / `max_price` / `hotel_class` 全部不出现。

---

## 3. `q` 是怎么拼出来的

由 [`build_query()`](../../app/agents/hotel_agent.py#L108) 生成，只有两种形态：

```python
f"{city.name}{area}附近酒店"   # 景点集中在某商圈时，area = business_area 的众数
f"{city.name}酒店"             # 景点分散时
```

实际值形如 `"成都市天府广场附近酒店"`。两点注意：

- **城市名带「市」**——它来自高德行政区查询（`CityRef.name`），不是用户原话。
- 用商圈拼比只用城市名**命中率高得多**：前者 Google 理解成"这一带的酒店"，
  后者只给市中心的大路货。

> **换数据源时这里值得重做。** 拼自然语言查询词是为了迁就 Google 的检索方式；
> 如果新源接受结构化入参（城市 ID / 经纬度 + 半径），直接用
> `state["dest_city"]`（含 adcode、citycode、中心坐标）和
> `attractions.centroid` 更准，也省掉了字符串拼接这层不确定性。

---

## 4. 本地校验：我们比 SerpAPI 严的地方

[`HotelSearchParams`](../../app/models/hotel.py#L158) 的校验器在**发请求之前**就拦下非法组合。
理由是配额——一个必然失败的请求同样扣额度。

| 规则 | 报错信息 | 为什么本地拦 |
|---|---|---|
| `check_out_date > check_in_date` | 离店必须晚于入住 | 反过来必然零结果 |
| `len(children_ages) == children` | 个数必须相等 | SerpAPI 会直接 400 |
| `1 ≤ 每个儿童年龄 ≤ 17` | 1 岁以下填 1 | 同上 |
| `q` 与 `property_token` 至少有一个 | 二选一 | 两个都空是无意义查询 |

### 条件发送的三条规则

`to_serpapi()` 里这几处 `if` 都有具体理由，不是随手写的：

1. **预算未填就不发 `max_price`。** 瞎设上限是空结果最常见的原因。
2. **`children=0` 时连 `children_ages` 一起不发。** 发空串会被判非法。
3. **`vacation_rentals=true` 时不发 `hotel_class` / `free_cancellation`。**
   这些 hotels 专属筛选在民宿模式下会被服务端**静默忽略**——
   与其发出去被丢掉，不如本地就不发，日志里也少一层误导。

---

## 5. 换数据源时怎么映射这些变量

新源的入参形态大概率不同，但**语义**是通用的。按重要性排：

| 语义 | 必须支持？ | 我们的值 | 换源时的注意点 |
|---|:---:|---|---|
| 地点 | ✅ | `q` 字符串 | 新源若收城市 ID / 坐标+半径，**改用结构化的更好** |
| 入住 / 离店日期 | ✅ | ISO 日期 | 注意是否含时区、是否要"晚数"而非离店日 |
| 成人数 | ✅ | int | **确认对方默认值**——Google 默认 2，我们默认 1 |
| 儿童数 + 年龄 | ⬜ | int + 逗号串 | 有的源要"每间房的儿童"而非"总儿童数" |
| 每晚价格上限 | ⬜ | int | 确认是**税前还是税后**、**每晚还是总价** |
| 星级 | ⬜ | 逗号串 | 有的源用 1–5，有的用"豪华/舒适/经济" |
| 币种 / 语言 / 地区 | ⬜ | ISO 码 | 必须与展示层一致，否则价格对不上 |

**两个最容易出错的点**：

1. **价格口径。** `max_price` 在 Google 这里是"每晚、税前、当前币种"。
   新源若是"总价"或"税后"，直接照搬会让筛选范围整体偏移。
2. **默认值差异。** 凡是我们与新源默认值不同的参数，一律**显式发送**——
   `adults` 就是现成的教训：Google 默认 2 而我们默认 1，不显式发的话
   单人行程会按双人报价，而这个错误在结果里几乎看不出来。

---

## 6. 速查：从用户输入到 HTTP 参数

| 用户填的 | `TripRequest` 字段 | → HTTP 参数 |
|---|---|---|
| 出发日期 | `outbound_date` | `check_in_date` |
| 返程日期 | `return_date` | `check_out_date` |
| 几个人 | `adults` | `adults` |
| 带几个小孩、多大 | `children` / `children_ages` | `children` / `children_ages` |
| 每晚预算 | `budget_per_night` | `max_price` |
| 想住几星 | `hotel_class` | `hotel_class` |
| 目的地 | `destination_city` → `CityRef.name` | 拼进 `q` |
| （不由用户决定） | `LocaleCtx` | `gl` / `hl` / `currency` |

`TripRequest` 里与酒店**无关**的字段：`departure_city`、`travel_class`、
`pace`、`transport`、`must_visit`、`avoid`——它们不参与酒店查询，
但 `must_visit` 会通过景点重心间接影响 `q` 里的商圈。

---

## 附：相关文件

| 文件 | 作用 |
|---|---|
| [`app/models/hotel.py`](../../app/models/hotel.py#L158) | `HotelSearchParams` + `to_serpapi()`，**变量定义与校验都在这** |
| [`app/tools/serpapi_hotels.py`](../../app/tools/serpapi_hotels.py#L219) | `hotels_search()` Tool 签名与 JSON Schema |
| [`app/agents/hotel_agent.py`](../../app/agents/hotel_agent.py#L141) | `search_hotels()`，决定实际传哪些 |
| [serpapi-google-hotels-api.md §3](serpapi-google-hotels-api.md#3-请求参数全表) | 官方参数全表（含我们没用的） |
| [hotel-provider-contract.md](hotel-provider-contract.md) | 输出侧契约（换源时配合本文一起读） |
