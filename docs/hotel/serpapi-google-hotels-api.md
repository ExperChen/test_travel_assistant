# SerpAPI Google Hotels API 工具文档

> 来源：<https://serpapi.com/google-hotels-api>（结合官方 playground 与真实返回）
> 上一环节工具：`serpapi-google-hotels-autocomplete-api.md`（输入补全 → 拿 property_token / autocomplete_suggestion）

---

## 1. 产品概述

Google Hotels API 是 **Google Hotels 搜索结果页的结构化抓取**，是 Better-travel-assistant 中 Hotel Agent 的「搜索主引擎」。和前一步 `engine=google_hotels_autocomplete` 配合形成完整链路：

```
用户模糊输入（"纽约 希尔顿"）
  ↓ google_hotels_autocomplete（补全）
用户选：[品牌] 或 [单门店(含 property_token)] 或 [搜索词]
  ↓ 本 API：engine=google_hotels
返回：brands（可过滤品牌ID） + ads（广告酒店） + properties（有机结果 hotel/vacation rental）
  + serpapi_pagination（翻页）
  ↓ 每条 property 自带 serpapi_property_details_link → 可进一步走 google_hotels_properties（单店完整房型）
```

**两种典型调用模式**：
- **A. 列表搜索（最常用）**：传 `q + check_in_date + check_out_date (+ adults/children/各种 filter)`，返回品牌树 + 一批广告 + 一批酒店/民宿，可翻页。
- **B. 单酒店查房价（Autocomplete 选了单门店就走这个）**：传上一步 Autocomplete 返回的 `property_token + q(地址)`，Google 会直接定位到这一家，返回其价格/评分/amenities/nearby_places。

**接口端点**：
```
GET https://serpapi.com/search.json?engine=google_hotels&q=...&check_in_date=YYYY-MM-DD&check_out_date=YYYY-MM-DD
```

Playground：<https://serpapi.com/playground?engine=google_hotels>

---

## 2. 原始代码 & JSON 示例（原封不动保留自官方）

> 下面代码和 JSON 为官方文档 `Bali Resorts` 示例 + `property_token=单酒店` 示例，原样照抄。

### 2.1 官方 GET URL（列表搜索）

```
https://serpapi.com/search.json?engine=google_hotels&q=Bali+Resorts&check_in_date=2026-07-30&check_out_date=2026-07-31
```

### 2.2 官方 Python 示例（列表搜索）

```python
import serpapi

client = serpapi.Client(api_key="secret_api_key")
results = client.search({
  "engine": "google_hotels",
  "q": "Bali Resorts",
  "check_in_date": "2026-07-30",
  "check_out_date": "2026-07-31"
})
properties = results["properties"]
```

> 本项目用 `.env` 里的 `SERPAPI_KEY`（代码里从 `settings.serpapi_key` 读取），不要用上面的 `secret_api_key`，也不要把真实 key 写进任何文件。

### 2.3 官方 Ruby 示例

```ruby
require "serpapi"

client = SerpApi::Client.new(
 engine: "google_hotels",
 q: "Bali Resorts",
 check_in_date: "2026-07-30",
 check_out_date: "2026-07-31",
 api_key: "secret_api_key" )

results = client.search
properties = results[:properties]
```

### 2.4 官方 GET（带完整参数 + vacation_rentals 示例）

```
https://serpapi.com/search.json?engine=google_hotels&q=Bali&vacation_rentals=true&check_in_date=2026-08-05&check_out_date=2026-08-06&adults=2&currency=USD&gl=us&hl=en
```

### 2.5 官方 JSON 示例（`q=Bali Resorts`，节选：search_metadata + search_parameters + brands 前 2 + ads 前 3 + properties 前 2 + serpapi_pagination）

```json
{
  "search_metadata": {
    "id": "695e5760fe1c6b0dfce576e7",
    "status": "Success",
    "json_endpoint": "https://serpapi.com/searches/53ea218ec31fbf8e/695e5760fe1c6b0dfce576e7.json",
    "created_at": "2026-01-07T12:53:52.165Z",
    "processed_at": "2026-01-07T12:53:52.171Z",
    "google_hotels_url": "https://www.google.com/_/TravelFrontendUi/data/batchexecute?rpcids=AtySUc&source-path=/travel/search&hl=en&gl=us&rt=c&soc-app=162&soc-platform=1&soc-device=1",
    "raw_html_file": "https://serpapi.com/searches/53ea218ec31fbf8e/695e5760fe1c6b0dfce576e7.html",
    "prettify_html_file": "https://serpapi.com/searches/53ea218ec31fbf8e/695e5760fe1c6b0dfce576e7.prettify",
    "total_time_taken": {
      "float": 2.6363649368286133
    }
  },
  "search_parameters": {
    "engine": "google_hotels",
    "q": "Bali Resorts",
    "gl": "us",
    "hl": "en",
    "currency": "USD",
    "check_in_date": "2026-04-08",
    "check_out_date": "2026-04-09",
    "adults": 2,
    "children": 0
  },
  "search_information": {
    "total_results": 15000
  },
  "brands": [
    {
      "id": 33,
      "name": "Accor Live Limitless",
      "children": [
        { "id": 67, "name": "Banyan Tree" },
        { "id": 101, "name": "Grand Mercure" },
        { "id": 452, "name": "Handwritten Collection" }
      ]
    },
    {
      "id": 223,
      "name": "Archipelago International",
      "children": [
        { "id": 229, "name": "Aston" },
        { "id": 225, "name": "Favehotel" }
      ]
    }
  ],
  "ads": [
    {
      "name": "Kalyssa Beach Bungalows",
      "source": "Booking.com",
      "source_icon": "https://www.gstatic.com/travel-hotels/branding/icon_184.png",
      "link": "https://www.google.com/aclk?sa=l&ai=...&adurl=",
      "property_token": "CgoIxLri6ry7-I5DEAE",
      "serpapi_property_details_link": "https://serpapi.com/search.json?adults=2&check_in_date=2026-04-08&check_out_date=2026-04-09&children=0&currency=USD&engine=google_hotels&gl=us&hl=en&property_token=CgoIxLri6ry7-I5DEAE&q=Bali+Resorts",
      "gps_coordinates": { "latitude": -8.139593999999999, "longitude": 114.650266 },
      "thumbnail": "https://lh4.googleusercontent.com/proxy/...=w273-h150-k-no",
      "overall_rating": 4.8,
      "reviews": 117,
      "price": "$70",
      "extracted_price": 70,
      "amenities": ["Beach access", "Pool", "Kid-friendly"],
      "free_cancellation": true
    },
    {
      "name": "Puri Mangga Sea View Resort & Spa",
      "source": "Booking.com",
      "source_icon": "https://www.gstatic.com/travel-hotels/branding/icon_184.png",
      "link": "https://www.google.com/aclk?sa=l&ai=...&adurl=",
      "property_token": "CgoI98f037uZvrIsEAE",
      "serpapi_property_details_link": "https://serpapi.com/search.json?adults=2&check_in_date=2026-04-08&check_out_date=2026-04-09&children=0&currency=USD&engine=google_hotels&gl=us&hl=en&property_token=CgoI98f037uZvrIsEAE&q=Bali+Resorts",
      "gps_coordinates": { "latitude": -8.179466999999999, "longitude": 115.04557699999998 },
      "hotel_class": 4,
      "thumbnail": "https://lh6.googleusercontent.com/proxy/...=w208-h150-k-no",
      "overall_rating": 4.6,
      "reviews": 139,
      "price": "$44",
      "extracted_price": 44,
      "amenities": ["Hot tub", "Spa", "Pool"],
      "free_cancellation": true
    }
  ],
  "properties": [
    {
      "type": "vacation rental",
      "name": "Le Sabot Ubud",
      "property_token": "ChoQ5YiTp-rKmO60ARoNL2cvMTFzc2djMTNqcRAC",
      "serpapi_property_details_link": "https://serpapi.com/search.json?adults=2&check_in_date=2026-04-08&check_out_date=2026-04-09&children=0&currency=USD&engine=google_hotels&gl=us&hl=en&property_token=ChoQ5YiTp-rKmO60ARoNL2cvMTFzc2djMTNqcRAC&q=Bali+Resorts",
      "gps_coordinates": { "latitude": -8.509249687194824, "longitude": 115.25045776367188 },
      "check_in_time": "3:00 PM",
      "check_out_time": "12:00 PM",
      "rate_per_night": {
        "lowest": "$114",
        "extracted_lowest": 114
      },
      "total_rate": {
        "lowest": "$114",
        "extracted_lowest": 114
      },
      "prices": [
        {
          "source": "Vio.com",
          "logo": "https://www.gstatic.com/travel-hotels/branding/....png",
          "num_guests": 2,
          "rate_per_night": { "lowest": "$114", "extracted_lowest": 114 }
        }
      ],
      "nearby_places": [
        {
          "name": "I Gusti Ngurah Rai International Airport",
          "transportations": [
            { "type": "Taxi", "duration": "1 hr 9 min" },
            { "type": "Public transport", "duration": "1 hr 40 min" }
          ]
        }
      ],
      "overall_rating": 4.4355693,
      "reviews": 123,
      "amenities": ["Air conditioning", "Kid-friendly", "Kitchen", "Outdoor pool", "Free Wi-Fi"],
      "excluded_amenities": ["No airport shuttle", "No beach access", "No fitness center"],
      "serpapi_google_hotels_reviews_link": "https://serpapi.com/search.json?engine=google_hotels_reviews&hl=en&property_token=ChoQ5YiTp-rKmO60ARoNL2cvMTFzc2djMTNqcRAC",
      "serpapi_google_hotels_photos_link": "https://serpapi.com/search.json?engine=google_hotels_photos&property_token=ChoQ5YiTp-rKmO60ARoNL2cvMTFzc2djMTNqcRAC"
    },
    {
      "type": "hotel",
      "name": "COMO Uma Canggu",
      "description": "Luxe hotel complex on the ocean, providing surf gear & classes for all ages, fine dining & a pool.",
      "link": "https://www.comohotels.com/umacanggu",
      "property_token": "ChoIv6zen9HPwIu2ARoNL2cvMTFoOXhudHk0YhAB",
      "serpapi_property_details_link": "https://serpapi.com/search.json?adults=2&check_in_date=2026-04-08&check_out_date=2026-04-09&children=0&currency=USD&engine=google_hotels&gl=us&hl=en&property_token=ChoIv6zen9HPwIu2ARoNL2cvMTFoOXhudHk0YhAB&q=Bali+Resorts",
      "gps_coordinates": { "latitude": -8.6546489, "longitude": 115.1258642 },
      "check_in_time": "3:00 PM",
      "check_out_time": "12:00 PM",
      "rate_per_night": {
        "lowest": "$198",
        "extracted_lowest": 198,
        "before_taxes_fees": "$165",
        "extracted_before_taxes_fees": 165
      },
      "total_rate": {
        "lowest": "$990",
        "extracted_lowest": 990,
        "before_taxes_fees": "$825",
        "extracted_before_taxes_fees": 825
      },
      "deal_description": "Great Deal",
      "nearby_places": [
        {
          "name": "Batu Bolong Beach",
          "transportations": [{ "type": "Taxi", "duration": "7 min" }]
        }
      ],
      "hotel_class": "5-star hotel",
      "extracted_hotel_class": 5,
      "overall_rating": 4.7,
      "reviews": 1554,
      "reviews_breakdown": [
        {
          "name": "Nature",
          "description": "Nature and outdoor activities",
          "total_mentioned": 177,
          "positive": 153,
          "negative": 9,
          "neutral": 15,
          "category_token": "2kxK7Hica1yRnFiSmp5fVDl5r19iSWlRqkJiXopCfmlJSn5-kUJicklmWWZJZmox4zKINAAIZRQx",
          "serpapi_link": "https://serpapi.com/search.json?category_token=2kxK7H...&engine=google_hotels_reviews&hl=en&property_token=ChoIv6zen9HPwIu2ARoNL2cvMTFoOXhudHk0YhAB"
        },
        {
          "name": "Service",
          "description": "Service",
          "total_mentioned": 244,
          "positive": 203,
          "negative": 27,
          "neutral": 14,
          "category_token": "DZ7RYHica1yRnFiSmp5fVDl5eXBqUVlmciojjAEAtX8MDA",
          "serpapi_link": "https://serpapi.com/search.json?category_token=DZ7RYH...&engine=google_hotels_reviews&hl=en&property_token=ChoIv6zen9HPwIu2ARoNL2cvMTFoOXhudHk0YhAB"
        }
      ],
      "amenities": ["Free breakfast", "Free Wi-Fi", "Spa", "Beach access", "Restaurant", "Room service"],
      "serpapi_google_hotels_reviews_link": "https://serpapi.com/search.json?engine=google_hotels_reviews&hl=en&property_token=ChoIv6zen9HPwIu2ARoNL2cvMTFoOXhudHk0YhAB",
      "serpapi_google_hotels_photos_link": "https://serpapi.com/search.json?engine=google_hotels_photos&property_token=ChoIv6zen9HPwIu2ARoNL2cvMTFoOXhudHk0YhAB"
    }
  ],
  "serpapi_pagination": {
    "current_from": 1,
    "current_to": 20,
    "next_page_token": "CBI=",
    "next": "https://serpapi.com/search.json?adults=2&check_in_date=2026-04-08&check_out_date=2026-04-09&children=0&currency=USD&engine=google_hotels&gl=us&hl=en&next_page_token=CBI%3D&q=Bali+Resorts"
  }
}
```

---

## 3. 请求参数全表

### 3.1 Search Query + 日期（必填）

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `engine` | ✅ | 固定 `google_hotels` |
| `q` | ✅* | 搜索关键词。可传任意 Google Hotels 查询（`"Bali Resorts"`、`"Hilton New York"`、`"上海迪士尼附近酒店"`）。**选了 Autocomplete 单门店时 q 仍然建议传（地址/门店名），但以 property_token 为准**。*列表模式必须 q 或 property_token 至少一个。 |
| `check_in_date` | ✅ | 入住日期，格式 `YYYY-MM-DD`（如 `2026-08-15`）。 |
| `check_out_date` | ✅ | 离店日期，格式 `YYYY-MM-DD`。必须晚于 check_in_date。 |

### 3.2 Localization（本地化）

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `gl` | 否 | 两位国家代码，`us`/`cn`/`jp`/`fr`… 影响酒店排序、品牌本地化。列表：<https://serpapi.com/google-countries> |
| `hl` | 否 | 两位语言代码，`en`/`zh-CN`/`ja`/`fr`… 影响返回描述/amenities 语言。列表：<https://serpapi.com/google-languages> |
| `currency` | 否 | 价格币种 ISO 代码，默认 `USD`；常用 `CNY` / `EUR` / `GBP` / `JPY`。列表：<https://serpapi.com/google-travel-currencies> |

### 3.3 入住人数（Advanced Parameters）

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `adults` | 否 | 成年人数，int，默认 `2`。 |
| `children` | 否 | 儿童人数，int，默认 `0`。 |
| `children_ages` | 否 | 每个儿童的年龄，逗号分隔；**个数必须 = children 参数值**。年龄范围 `1`~`17`，1 岁以下填 `1`。例：`children=2` + `children_ages=5,8`。 |

### 3.4 高级筛选（Advanced Filters，列表搜索建议开启）

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `sort_by` | 否 | 排序。可选：`3`=最低价、`8`=最高评分、`13`=评论最多；默认（不传）= Relevance 相关度。 |
| `min_price` / `max_price` | 否 | 每晚价格区间（整数，**币种 = currency 参数**）。例：`min_price=500&max_price=2000`。 |
| `property_types` | 否 | 物业类型 ID，逗号分隔。Hotels 类型：<https://serpapi.com/google-hotels-property-types>；Vacation Rentals 类型：<https://serpapi.com/google-hotels-vacation-rentals-property-types>。常用：17=Resort、12=Hotel、18=Villa。 |
| `amenities` | 否 | 设施 ID，逗号分隔。Hotels 设施：<https://serpapi.com/google-hotels-amenities>；Vacation Rentals 设施：<https://serpapi.com/google-hotels-vacation-rentals-amenities>。常用：35=Free Wi-Fi、9=Pool、19=Free parking。 |
| `rating` | 否 | 最低评分：`7`=3.5+ / `8`=4.0+ / `9`=4.5+。 |

### 3.5 Hotels 专属 Filters（仅当 `vacation_rentals` 未开启时有效）

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `brands` | 否 | 品牌 ID，逗号分隔。**ID 来源：本次或上次搜索返回的 `brands[].id / brands[].children[].id`**（例：33=Accor，67=Banyan Tree）。 |
| `hotel_class` | 否 | 星级：`2`/`3`/`4`/`5`；可多选逗号分隔，如 `hotel_class=4,5`（四星+五星）。 |
| `free_cancellation` | 否 | 传 `true` 只显示支持免费取消的酒店。 |
| `special_offers` | 否 | 传 `true` 只显示有特价/活动标签的酒店。 |
| `eco_certified` | 否 | 传 `true` 只显示环保认证酒店。 |

### 3.6 Vacation Rentals 专属 Filters（需先开 `vacation_rentals=true`）

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `vacation_rentals` | 否 | 传 `true` 切换到「Vacation Rentals 民宿/公寓」模式，默认 `false`=Hotels。切到此模式后 3.5 的 hotels 专属 filters 全部失效。 |
| `bedrooms` | 否 | 最少卧室数（int），默认 `0`。 |
| `bathrooms` | 否 | 最少卫浴数（int），默认 `0`。 |

### 3.7 Pagination + Property Token

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `next_page_token` | 否 | 翻页。从上一次返回的 `serpapi_pagination.next_page_token` 原样拿；同时其他所有参数必须和上一次完全一致。 |
| `property_token` | 否 | **单酒店查房价模式**。从 Autocomplete 的 `suggestions[].property_token` 或本 API `properties[].property_token` / `ads[].property_token` 拿到。传了之后 Google 会定位到该具体酒店（此时 q 可传地址作为 fallback）。 |

### 3.8 SerpAPI 通用参数

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `api_key` | ✅ | SerpAPI Key，本项目 `.env` 里 `SERPAPI_KEY`。 |
| `no_cache` | 否 | `true`=绕过 1 小时缓存（扣额度）；默认 `false`=优先缓存（免费不扣）。 |
| `async` | 否 | `true`=异步提交后去 Searches Archive 取结果；默认 `false`=同步阻塞。**不和 no_cache 同开**。 |
| `zero_trace` | 否 | Enterprise 专属，`true`=不落盘搜索参数，调试更难。默认 `false`。 |
| `output` | 否 | `json`(默认) / `html`。建议 json。 |
| `json_restrictor` | 否 | 指定只返回某些顶层字段（如 `properties,ads`）以减小响应体积。 |

---

## 4. 返回字段详解

### 4.1 顶层结构总览

```json
{
  "search_metadata":  { "id": "...", "status": "Success", "total_time_taken": { "float": 2.6 } },
  "search_parameters":{ "engine":"google_hotels", "q":"...", "check_in_date":"...", "...": "..." },
  "search_information":{ "total_results": 15000 },
  "brands":           [ { "id": 33, "name": "Accor", "children": [{ "id":67, "name":"Banyan Tree" }] } ],
  "ads":              [ { "name": "...", "source": "Booking.com", "price": "$44", "extracted_price": 44, "..." : "..." } ],
  "properties":       [ { "type": "hotel" | "vacation rental", "name": "...", "rate_per_night": {...}, "..." : "..." } ],
  "serpapi_pagination":{ "current_from": 1, "current_to": 20, "next_page_token": "CBI=", "next": "..." }
}
```

各顶层字段的 Agent 含义：

| 字段 | 类型 | 说明 |
|------|------|------|
| `search_metadata.status` | string | `Processing → Success/Error`；失败会有 `error` 字段。id 可用作排障凭证。 |
| `search_parameters` | object | 回显的请求参数，建议在日志里打印核对「我发的和 SerpAPI 收到的一致吗」。 |
| `search_information.total_results` | int | Google 总命中数。可用于给用户提示「共找到约 X 万条」。 |
| `brands[]` | Brand[] | **品牌树**：下次请求想只看 Accor 系就传 `brands=33,67`。只有 Hotels 模式才有。 |
| `ads[]` | AdProperty[] | 赞助广告酒店（Booking/Expedia 等渠道），位置靠前、价格/评分通常也不错，**不要过滤掉**。 |
| `properties[]` | HotelProperty \| VRBOProperty[] | 有机结果，分 `type=hotel` 和 `type=vacation rental` 两种子结构。 |
| `serpapi_pagination` | object | 翻页凭证。`next_page_token` 原样回传即可翻下一页；没这字段就是最后一页。 |

### 4.2 `brands[]` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 品牌 ID，下次请求 `brands=33,67` 过滤用。**这个值跨次请求稳定吗？官方文档说 ID 来源就是 brands 数组本身，建议每次搜索时重建映射，不要硬编码**。 |
| `name` | string | 品牌组名（如 "Accor Live Limitless"、"Marriott Bonvoy"）。 |
| `children[]` | BrandChild[] | 子品牌数组，含 `id` + `name`。层级可能不止一层，需递归展开。 |

### 4.3 `ads[]` 字段（广告酒店）

ads 展示位置和有机 properties 相同但字段更精简，是"价格胶囊卡片"形态：

| 字段 | 频率 | 类型 | 说明 |
|------|:----:|------|------|
| `name` | ✅ | string | 酒店名。 |
| `source` | ✅ | string | 预订渠道来源（Booking.com / Expedia / Agoda 等）。 |
| `source_icon` | ⭕ | string | 渠道 logo URL，UI 用。 |
| `link` | ✅ | string | 跳 Google 的广告中转 URL，想直接跳 Booking 点它。 |
| `property_token` | ✅ | string | **下一步调用单店详情用**（和有机 properties 同语义）。 |
| `serpapi_property_details_link` | ✅ | string | 已预填 property_token+日期的 google_hotels URL，可直接 parse_qs 转下次参数。 |
| `gps_coordinates` | ✅ | {latitude, longitude} | 经纬度，可用来在地图上打点。 |
| `thumbnail` | ⭕ | string | 酒店缩略图。 |
| `hotel_class` | ⭕ | int | 星级（2/3/4/5），广告卡上可能有星星图标。 |
| `overall_rating` | ✅ | float | Google 聚合评分 0~5。 |
| `reviews` | ✅ | int | 评论条数。 |
| `price` / `extracted_price` | ✅ | string / int | 广告显示价格（带货币符号 / 纯数字）。**注意 ads 的价格是「单晚」还是「总晚数」？通常 ads 显示的是列表页单晚最低，但以实际点进去为准**。 |
| `amenities[]` | ⭕ | string[] | 文本化设施名（不是 ID）。 |
| `free_cancellation` | ⭕ | bool | 是否支持免费取消。 |

### 4.4 `properties[]` 字段（有机结果，两种子类型）

**通用字段（两种 type 都有）**：

| 字段 | 频率 | 类型 | 说明 |
|------|:----:|------|------|
| `type` | ✅ | `"hotel"` \| `"vacation rental"` | **第一分支条件**：决定后面字段集。 |
| `name` | ✅ | string | 酒店/民宿名。 |
| `property_token` | ✅ | string | 单店详情 token（下次 `engine=google_hotels_properties` 或再调 `google_hotels + property_token` 都能用）。 |
| `serpapi_property_details_link` | ✅ | string | 预填好的单店详情 URL（可直接 parse_qs 转参数）。 |
| `gps_coordinates` | ✅ | {latitude, longitude} | 经纬度。 |
| `check_in_time` / `check_out_time` | ⭕ | string | 入住/退房时间（如 `"3:00 PM"` / `"12:00 PM"`）。 |
| `rate_per_night` | ✅ | Rate | 单晚价格对象：`lowest`(带符号) / `extracted_lowest`(int) / 可选 `before_taxes_fees` / `extracted_before_taxes_fees`。 |
| `total_rate` | ✅ | Rate | **整段入住总价**（= 单晚 × 晚数，是否含税看字段），推荐给用户看这个；结构同 rate_per_night。 |
| `prices[]` | ⭕ | PriceFromSource[] | 各渠道价格明细：`source`/`logo`/`num_guests`/`rate_per_night{...}`，可用来展示"在 Vio.com 上 $68/晚，在 Booking.com 上 $72/晚"。 |
| `nearby_places[]` | ⭕ | NearbyPlace[] | 周边地标：`name` + `transportations[] = [{type:"Taxi"/"Public transport"/"Walk", duration:"7 min"}]`。对"离海滩/机场/地铁站多远"特别有用。 |
| `overall_rating` | ✅ | float | 评分（0~5，可能高精度如 4.4355693，展示时四舍五入到 1 位小数）。 |
| `reviews` | ✅ | int | 评论总数。 |
| `amenities[]` | ✅ | string[] | 含设施（文本）。 |
| `excluded_amenities[]` | ⭕ | string[] | 不含设施（"No airport shuttle" 等），对用户决策很重要（"我要接机要避开这类"）。 |
| `serpapi_google_hotels_reviews_link` | ✅ | string | 下一步 `engine=google_hotels_reviews` 接口 URL，可拉具体评论。 |
| `serpapi_google_hotels_photos_link` | ✅ | string | 下一步 `engine=google_hotels_photos` 接口 URL，可拉图集。 |

**仅 `type: "hotel"` 额外字段**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `description` | string | Google 简介一句话（如 "Luxe hotel complex on the ocean, ..."）。 |
| `link` | string | 酒店官网或 Google 商家页链接。 |
| `deal_description` | string | 价签标签（如 "Great Deal"、"Today's Deal"），UI 上可高亮胶囊。 |
| `hotel_class` / `extracted_hotel_class` | string / int | 星级：`"5-star hotel"` / `5`。用 extracted 做数值比较更方便。 |
| `reviews_breakdown[]` | ReviewCategory[] | 评论维度拆解：`name`（"Service"/"Nature"/"Breakfast"…）/ `description` / `total_mentioned` / `positive` / `negative` / `neutral` / `category_token` / `serpapi_link`。**可用来判断"这家酒店服务口碑好吗/早餐评价差吗"**。 |

**仅 `type: "vacation rental"` 额外字段**：

vacation rental 基本没有 hotel 专属字段；但因是整套房源，amenities 里会更常出现 `Kitchen`、`Washer`、`Crib` 等。

### 4.5 `serpapi_pagination` 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `current_from` / `current_to` | int | 当前页展示的结果序号（1-based）。 |
| `next_page_token` | string | 下次请求 `next_page_token=` 原样传。**注意：其他参数必须完全相同，否则 token 失效**。 |
| `next` | string | SerpAPI 已拼好的下一页完整 URL，可直接 GET。 |

> 最后一页：`serpapi_pagination` 字段缺失，或存在但无 `next_page_token`/`next`。

---

## 5. Python 完整工具示例（读取本项目 .env）

```python
"""
SerpAPI Google Hotels 搜索工具
依赖: pip install serpapi python-dotenv
"""
import os
from dataclasses import dataclass, field
from typing import Literal, Optional
from urllib.parse import urlparse, parse_qs

import serpapi
from dotenv import load_dotenv

load_dotenv()
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")

# ================ 类型定义 ================
PropertyKind = Literal["hotel", "vacation_rental"]


@dataclass
class Rate:
    lowest_str: Optional[str] = None
    lowest: Optional[int] = None               # extracted_lowest
    before_taxes_str: Optional[str] = None
    before_taxes: Optional[int] = None         # extracted_before_taxes_fees


@dataclass
class PriceSource:
    source: str
    logo: Optional[str] = None
    num_guests: Optional[int] = None
    rate_per_night: Optional[Rate] = None


@dataclass
class Transportation:
    type: str          # Taxi / Public transport / Walk ...
    duration: str      # "7 min"


@dataclass
class NearbyPlace:
    name: str
    transportations: list[Transportation] = field(default_factory=list)


@dataclass
class ReviewCategory:
    name: str
    description: Optional[str] = None
    total_mentioned: int = 0
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    category_token: Optional[str] = None
    serpapi_link: Optional[str] = None


@dataclass
class HotelProperty:
    kind: PropertyKind
    name: str
    property_token: str
    serpapi_property_details_link: str
    latitude: Optional[float]
    longitude: Optional[float]
    check_in_time: Optional[str]
    check_out_time: Optional[str]
    rate_per_night: Rate
    total_rate: Rate
    prices: list[PriceSource]
    nearby_places: list[NearbyPlace]
    overall_rating: Optional[float]
    reviews: Optional[int]
    amenities: list[str]
    excluded_amenities: list[str]
    reviews_link: Optional[str]
    photos_link: Optional[str]
    # hotel-only:
    description: Optional[str] = None
    link: Optional[str] = None
    deal_description: Optional[str] = None
    hotel_class: Optional[int] = None   # extracted_hotel_class
    reviews_breakdown: list[ReviewCategory] = field(default_factory=list)


@dataclass
class AdProperty:
    name: str
    source: str
    ad_link: str
    property_token: str
    serpapi_property_details_link: str
    latitude: Optional[float]
    longitude: Optional[float]
    overall_rating: Optional[float]
    reviews: Optional[int]
    price_str: Optional[str]
    price: Optional[int]
    amenities: list[str]
    thumbnail: Optional[str] = None
    hotel_class: Optional[int] = None
    free_cancellation: Optional[bool] = None


@dataclass
class HotelSearchResults:
    status: str
    search_id: str
    total_results: Optional[int]
    brands_flat: dict[int, str]            # id -> name 扁平映射，下一次 brands=xxx,yyy 过滤用
    ads: list[AdProperty]
    properties: list[HotelProperty]
    next_page_token: Optional[str]
    next_page_url: Optional[str]

# ================ 核心解析函数 ================

def _parse_rate(d: Optional[dict]) -> Rate:
    if not d:
        return Rate()
    return Rate(
        lowest_str=d.get("lowest"),
        lowest=d.get("extracted_lowest"),
        before_taxes_str=d.get("before_taxes_fees"),
        before_taxes=d.get("extracted_before_taxes_fees"),
    )


def _flatten_brands(brands: Optional[list[dict]]) -> dict[int, str]:
    out: dict[int, str] = {}
    def walk(items):
        for b in items or []:
            bid = b.get("id")
            if isinstance(bid, int):
                out[bid] = b.get("name", "")
            walk(b.get("children") or [])
    walk(brands or [])
    return out


def _parse_properties(properties: Optional[list[dict]]) -> list[HotelProperty]:
    out: list[HotelProperty] = []
    for p in properties or []:
        t_raw = p.get("type")
        kind: PropertyKind = "vacation_rental" if t_raw == "vacation rental" else "hotel"
        gps = p.get("gps_coordinates") or {}
        prices: list[PriceSource] = []
        for ps in p.get("prices") or []:
            prices.append(PriceSource(
                source=ps.get("source", ""),
                logo=ps.get("logo"),
                num_guests=ps.get("num_guests"),
                rate_per_night=_parse_rate(ps.get("rate_per_night")),
            ))
        places: list[NearbyPlace] = []
        for np_ in p.get("nearby_places") or []:
            trans = [Transportation(type=t.get("type", ""), duration=t.get("duration", ""))
                     for t in (np_.get("transportations") or [])]
            places.append(NearbyPlace(name=np_.get("name", ""), transportations=trans))
        rb: list[ReviewCategory] = []
        for r in p.get("reviews_breakdown") or []:
            rb.append(ReviewCategory(
                name=r.get("name", ""),
                description=r.get("description"),
                total_mentioned=int(r.get("total_mentioned") or 0),
                positive=int(r.get("positive") or 0),
                negative=int(r.get("negative") or 0),
                neutral=int(r.get("neutral") or 0),
                category_token=r.get("category_token"),
                serpapi_link=r.get("serpapi_link"),
            ))
        out.append(HotelProperty(
            kind=kind,
            name=p.get("name", ""),
            property_token=p.get("property_token", ""),
            serpapi_property_details_link=p.get("serpapi_property_details_link", ""),
            latitude=gps.get("latitude"),
            longitude=gps.get("longitude"),
            check_in_time=p.get("check_in_time"),
            check_out_time=p.get("check_out_time"),
            rate_per_night=_parse_rate(p.get("rate_per_night")),
            total_rate=_parse_rate(p.get("total_rate")),
            prices=prices,
            nearby_places=places,
            overall_rating=p.get("overall_rating"),
            reviews=p.get("reviews"),
            amenities=list(p.get("amenities") or []),
            excluded_amenities=list(p.get("excluded_amenities") or []),
            reviews_link=p.get("serpapi_google_hotels_reviews_link"),
            photos_link=p.get("serpapi_google_hotels_photos_link"),
            # hotel-only:
            description=p.get("description"),
            link=p.get("link"),
            deal_description=p.get("deal_description"),
            hotel_class=p.get("extracted_hotel_class"),
            reviews_breakdown=rb,
        ))
    return out


def _parse_ads(ads: Optional[list[dict]]) -> list[AdProperty]:
    out = []
    for a in ads or []:
        gps = a.get("gps_coordinates") or {}
        out.append(AdProperty(
            name=a.get("name", ""),
            source=a.get("source", ""),
            ad_link=a.get("link", ""),
            property_token=a.get("property_token", ""),
            serpapi_property_details_link=a.get("serpapi_property_details_link", ""),
            latitude=gps.get("latitude"),
            longitude=gps.get("longitude"),
            overall_rating=a.get("overall_rating"),
            reviews=a.get("reviews"),
            price_str=a.get("price"),
            price=a.get("extracted_price"),
            amenities=list(a.get("amenities") or []),
            thumbnail=a.get("thumbnail"),
            hotel_class=a.get("hotel_class"),
            free_cancellation=a.get("free_cancellation"),
        ))
    return out

# ================ 对外调用函数 ================

def google_hotels_search(
    q: Optional[str] = None,
    check_in_date: Optional[str] = None,   # YYYY-MM-DD 必填（除非 property_token 查单店也建议填）
    check_out_date: Optional[str] = None,  # YYYY-MM-DD 必填
    *,
    # 人数
    adults: int = 2,
    children: int = 0,
    children_ages: Optional[list[int]] = None,
    # 本地化
    gl: Optional[str] = None,
    hl: str = "en",
    currency: str = "USD",
    # 筛选
    sort_by: Optional[Literal[3, 8, 13]] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    property_types: Optional[list[int | str]] = None,
    amenities: Optional[list[int | str]] = None,
    rating: Optional[Literal[7, 8, 9]] = None,
    brands: Optional[list[int]] = None,
    hotel_class: Optional[list[int]] = None,
    free_cancellation: bool = False,
    special_offers: bool = False,
    eco_certified: bool = False,
    # Vacation Rentals
    vacation_rentals: bool = False,
    bedrooms: Optional[int] = None,
    bathrooms: Optional[int] = None,
    # 单酒店 / 翻页
    property_token: Optional[str] = None,
    next_page_token: Optional[str] = None,
    # SerpAPI 通用
    no_cache: bool = False,
) -> HotelSearchResults:
    """
    调用 engine=google_hotels，解析成结构化结果。
    两种用法：
      1. 列表搜索：传 q + check_in_date + check_out_date → 多结果
      2. 单酒店查房价：传 property_token (+ q 作 fallback) + 日期 → 通常 1 条精准命中
    """
    if not SERPAPI_KEY:
        raise RuntimeError("缺少 SERPAPI_KEY，请先在 .env 填入")
    if not (check_in_date and check_out_date):
        raise ValueError("check_in_date 和 check_out_date 必填（YYYY-MM-DD）")
    if not q and not property_token:
        raise ValueError("q 和 property_token 至少传一个")
    if children and children_ages and len(children_ages) != children:
        raise ValueError(f"children={children}，但 children_ages 长度={len(children_ages or [])}，必须一致")

    params: dict = {
        "engine": "google_hotels",
        "check_in_date": check_in_date,
        "check_out_date": check_out_date,
        "adults": int(adults),
        "children": int(children),
        "hl": hl,
        "currency": currency,
    }
    if q: params["q"] = q
    if gl: params["gl"] = gl
    if children_ages:
        params["children_ages"] = ",".join(str(x) for x in children_ages)

    if sort_by is not None: params["sort_by"] = int(sort_by)
    if min_price is not None: params["min_price"] = int(min_price)
    if max_price is not None: params["max_price"] = int(max_price)
    if property_types: params["property_types"] = ",".join(str(x) for x in property_types)
    if amenities: params["amenities"] = ",".join(str(x) for x in amenities)
    if rating is not None: params["rating"] = int(rating)

    if brands: params["brands"] = ",".join(str(x) for x in brands)
    if hotel_class: params["hotel_class"] = ",".join(str(x) for x in hotel_class)
    if free_cancellation: params["free_cancellation"] = "true"
    if special_offers: params["special_offers"] = "true"
    if eco_certified: params["eco_certified"] = "true"

    if vacation_rentals:
        params["vacation_rentals"] = "true"
        if bedrooms is not None: params["bedrooms"] = int(bedrooms)
        if bathrooms is not None: params["bathrooms"] = int(bathrooms)

    if property_token: params["property_token"] = property_token
    if next_page_token: params["next_page_token"] = next_page_token
    if no_cache: params["no_cache"] = "true"

    client = serpapi.Client(api_key=SERPAPI_KEY)
    raw = client.search(params)

    meta = raw.get("search_metadata") or {}
    info = raw.get("search_information") or {}
    pag = raw.get("serpapi_pagination") or {}

    return HotelSearchResults(
        status=meta.get("status", "Unknown"),
        search_id=meta.get("id", ""),
        total_results=info.get("total_results"),
        brands_flat=_flatten_brands(raw.get("brands")),
        ads=_parse_ads(raw.get("ads")),
        properties=_parse_properties(raw.get("properties")),
        next_page_token=pag.get("next_page_token"),
        next_page_url=pag.get("next"),
    )


def extract_hotels_params_from_link(link: str) -> dict:
    """
    （复用 Autocomplete 文档里的同名函数）
    从 serpapi_property_details_link / serpapi_google_hotels_link 反解出参数字典。
    用法示例：
        params = extract_hotels_params_from_link(prop.serpapi_property_details_link)
        params["check_in_date"] = "2026-09-01"   # 覆盖默认日期
        params["check_out_date"] = "2026-09-05"
        results = client.search({"engine":"google_hotels", **params})
    """
    query = urlparse(link).query
    params = {k: (v[0] if len(v) == 1 else v) for k, v in parse_qs(query).items()}
    params.pop("engine", None)
    return params

# ================ 运行示例 ================

if __name__ == "__main__":
    from datetime import date, timedelta
    today = date.today()
    check_in = (today + timedelta(days=30)).isoformat()
    check_out = (today + timedelta(days=35)).isoformat()

    # 场景 A: 巴厘岛 4 星以上 + 免费取消 + 评分 4.0+，按最低价排序
    print(f"===== 列表搜索: Bali Resorts 4-star+ free_cancel rating≥4.0 =====")
    r = google_hotels_search(
        q="Bali Resorts",
        check_in_date=check_in,
        check_out_date=check_out,
        gl="us", hl="en", currency="USD",
        adults=2,
        hotel_class=[4, 5],
        free_cancellation=True,
        rating=8,
        sort_by=3,
    )
    print(f"status={r.status} total≈{r.total_results} brands_count={len(r.brands_flat)} "
          f"ads={len(r.ads)} properties={len(r.properties)} next_token={r.next_page_token}")

    # 展示前 3 个 organic properties
    for idx, p in enumerate(r.properties[:3], 1):
        star = f"{'★' * p.hotel_class}" if p.hotel_class else ("🏡" if p.kind == "vacation_rental" else "🏨")
        price_str = p.total_rate.lowest_str or p.rate_per_night.lowest_str or "N/A"
        rating = f"{p.overall_rating:.1f}" if p.overall_rating else "?"
        print(f"  {idx}. {star} {p.name}  总价 {price_str}  评分 {rating}/5 ({p.reviews}评)")
        if p.deal_description:
            print(f"     💡 {p.deal_description}")
        # 最近一个附近机场/海滩
        if p.nearby_places:
            np0 = p.nearby_places[0]
            t0 = np0.transportations[0] if np0.transportations else None
            if t0:
                print(f"     📍 距 {np0.name}: {t0.type} {t0.duration}")

    # 如果有品牌，示例：只看第一个品牌再次搜索
    if r.brands_flat:
        first_brand_id, first_brand_name = next(iter(r.brands_flat.items()))
        print(f"\n===== 再次搜索，限定品牌 {first_brand_name} (id={first_brand_id}) =====")
        r2 = google_hotels_search(
            q="Bali Resorts",
            check_in_date=check_in,
            check_out_date=check_out,
            brands=[first_brand_id],
        )
        print(f"  brands_only properties={len(r2.properties)} ads={len(r2.ads)}")

    # 场景 B: 如果有第一条 property，拿它的 property_token 做单店精准查询
    if r.properties:
        p0 = r.properties[0]
        print(f"\n===== 单店查询: {p0.name} (property_token 后 12 位 = ...{p0.property_token[-12:]}) =====")
        r3 = google_hotels_search(
            q=p0.name,
            property_token=p0.property_token,
            check_in_date=check_in,
            check_out_date=check_out,
        )
        print(f"  返回 properties 条数={len(r3.properties)}")
        if r3.properties:
            s = r3.properties[0]
            if s.reviews_breakdown:
                top_cat = sorted(s.reviews_breakdown, key=lambda c: -c.total_mentioned)[:3]
                print(f"  评论维度 TOP3:")
                for c in top_cat:
                    print(f"    - {c.name}: +{c.positive} -{c.negative} ±{c.neutral} (total {c.total_mentioned})")
```

---

## 6. 在 Travel Assistant 中作为 Hotel Agent Tool 的设计建议

Google Hotels 搜索是 **Hotel Agent 的第二个 Tool**（第一个是 `serpapi_google_hotels_autocomplete`，负责输入补全让用户选品牌/门店/搜索词）。推荐封装成两个入口合一的 Tool：

### 6.1 Tool: `serpapi_google_hotels_search`

#### Tool Schema

```json
{
  "name": "serpapi_google_hotels_search",
  "description": "当用户已经提供了（或 Agent 已收集到）入住/离店日期、城市/搜索词/已选择的 Autocomplete 建议后，调用此工具进行酒店搜索。支持两种模式：(1) 列表搜索模式：传 q + check_in_date + check_out_date，返回 brands（品牌ID映射）+ ads（广告酒店）+ properties（有机结果 hotel/vacation rental 两种 type）+ 分页 next_page_token；(2) 单店精准模式：如果用户从 Autocomplete 选择了有 property_token 的具体门店，就把 property_token 和 q（门店地址/名）一起传进来，返回的 properties[0] 就是该酒店专属房价详情。注意：无论哪种模式，check_in_date + check_out_date（YYYY-MM-DD）永远必填；返回总价看 total_rate，单晚看 rate_per_night；每条 property 自带 serpapi_property_details_link（可用于下一轮再解析或进入 google_hotels_properties）。",
  "parameters": {
    "type": "object",
    "properties": {
      "q":                  { "type": "string",  "description": "搜索关键词（城市/区域/酒店品牌/Autocomplete 返回的 autocomplete_suggestion）。单店模式也建议传门店名或地址。" },
      "property_token":     { "type": "string",  "description": "可选，单店模式必填。来自 Autocomplete 的 suggestions[].property_token 或上次搜索的 properties[].property_token。" },
      "check_in_date":      { "type": "string",  "description": "入住日期，必填，格式 YYYY-MM-DD。" },
      "check_out_date":     { "type": "string",  "description": "离店日期，必填，格式 YYYY-MM-DD。" },
      "adults":             { "type": "integer", "description": "成年人数，默认 2。" },
      "children":           { "type": "integer", "description": "儿童人数，默认 0。" },
      "children_ages":      { "type": "array",   "items": { "type": "integer" }, "description": "每个儿童的年龄列表（1-17），长度必须等于 children。" },
      "gl":                 { "type": "string",  "description": "两位国家代码，能从上下文判断就填。" },
      "hl":                 { "type": "string",  "description": "语言代码，默认 en，中文用户填 zh-CN。" },
      "currency":           { "type": "string",  "description": "币种 ISO 代码，默认 USD，中国用户可填 CNY。" },
      "sort_by":            { "type": "integer", "enum": [3, 8, 13], "description": "排序：3=最低价 8=最高评分 13=评论最多。" },
      "min_price":          { "type": "integer", "description": "单晚最低价（整数，按 currency）。" },
      "max_price":          { "type": "integer", "description": "单晚最高价（整数，按 currency）。" },
      "rating":             { "type": "integer", "enum": [7, 8, 9], "description": "最低评分门槛：7=3.5+ 8=4.0+ 9=4.5+" },
      "brands":             { "type": "array",   "items": { "type": "integer" }, "description": "要限定的品牌 ID 列表（值来自本次或上次返回的 brands_flat 的 key）。" },
      "hotel_class":        { "type": "array",   "items": { "type": "integer", "enum": [2,3,4,5] }, "description": "星级列表（仅 hotels 模式有效）。" },
      "free_cancellation":  { "type": "boolean", "description": "仅显示支持免费取消的酒店（仅 hotels 模式）。" },
      "vacation_rentals":   { "type": "boolean", "description": "true=切换到 Vacation Rentals 民宿/公寓模式（这时星级/免费取消等 hotels 专属参数会失效）。" },
      "bedrooms":           { "type": "integer", "description": "最少卧室数（仅 vacation_rentals=true 时有效）。" },
      "bathrooms":          { "type": "integer", "description": "最少卫浴数（仅 vacation_rentals=true 时有效）。" },
      "next_page_token":    { "type": "string",  "description": "翻页 token（来自上次返回的 next_page_token），其他参数必须完全一致。" }
    },
    "required": ["check_in_date", "check_out_date"]
  }
}
```

### 6.2 典型 Agent 交互流程（接 Autocomplete 之后）

```
（承接 Autocomplete 文档第 6.2 节：用户已选第 3 家 Hilton Times Square，带 property_token）

User: "我要从 9 月 1 日住到 9 月 5 日，2 大人 1 个 7 岁小孩，免费取消最好"
  ↓
Thought: 用户已补齐日期+人数+偏好。之前选了 Hilton Times Square 单门店（有 property_token），
         应走单店模式（property_token + dates + adults/children + free_cancellation）。
Action: serpapi_google_hotels_search
Action Input: {
  "q": "Hilton Times Square 234 W 42nd St, New York",
  "property_token": "ChgI3N7hv9WH9q4DGgwvZy8xaGY4X3MzM3EQAQ",
  "check_in_date": "2026-09-01",
  "check_out_date": "2026-09-05",
  "adults": 2,
  "children": 1,
  "children_ages": [7],
  "gl": "us", "hl": "en", "currency": "USD",
  "free_cancellation": true
}
  ↓
Observation:
  properties[0] = {
    kind: hotel, name: "Hilton Times Square",
    total_rate: {lowest: "$1,820", extracted_lowest: 1820,
                 before_taxes_fees: "$1,520", extracted_before_taxes_fees: 1520},
    rate_per_night: {lowest: "$455"},
    hotel_class: 4, overall_rating: 4.5, reviews: 12893,
    deal_description: "Great Deal",
    nearby_places: [{name:"Times Square", transportations:[{type:"Walk", duration:"2 min"}]},
                    {name:"LaGuardia Airport", transportations:[{type:"Taxi", duration:"25 min"}]}],
    amenities: ["Free Wi-Fi","Fitness center","Restaurant","Room service"],
    reviews_breakdown: [
      {name:"Location", total:812, +:780, -:15},
      {name:"Cleanliness", total:640, +:590, -:28},
      {name:"Service", total:510, +:430, -:48}
    ]
  }
  ↓
Agent Response:
  已为您锁定 Hilton Times Square（4★），2026-09-01 → 09-05（4晚），2大1小（7岁）：
  📍 Times Square 步行 2 分钟 · LaGuardia 机场打车 25 分钟
  💰 总价（含税费预估） $1,820 USD（税前 $1,520，约 $455/晚） · ✅ Great Deal · ✅ 可免费取消
  ⭐ 4.5/5 · 12,893 条评价
     - 位置：812人提及，780条好评（"就在时代广场，晚上也热闹"）
     - 卫生：640人提及，590条好评
     - 服务：510人提及，430条好评
  设施：Wi-Fi / 健身中心 / 餐厅 / 客房服务

  接下来可以：
  ① 查看更多渠道比价（调用 serpapi_property_details_link 的 prices[]）
  ② 看具体房型（下一步 engine=google_hotels_properties）
  ③ 换一家酒店（回到 Autocomplete 重新选）
```

---

## 7. 注意事项 & 坑

1. **check_in_date / check_out_date 是必填**，哪怕 `property_token` 模式也必须传日期（否则 Google 不知道你要查哪一晚的房价）。日期格式严格 `YYYY-MM-DD`，`2026-9-1` 会失败，必须补零成 `2026-09-01`。

2. **children_ages 数量必须 = children 参数**。如果你写了 `children=2` 但 `children_ages=5`（只写 1 个），SerpAPI 会返回参数错误。儿童年龄范围 `1-17`，1 岁以下的婴儿填 `1`。

3. **Autocomplete 反解参数时一定要覆盖日期、人数**。`serpapi_google_hotels_link` / `serpapi_property_details_link` 里面预填的永远是「今天+1 晚 + adults=2」，必须用 `extract_hotels_params_from_link` 解出来后覆盖 `check_in_date / check_out_date / adults / children / children_ages / currency / gl / hl`。

4. **currency / gl / hl 三参数建议在一次会话中保持恒定**。Autocomplete 用了 CNY，搜索这里也必须 CNY，否则 Autocomplete 展示的价格区间和搜索返回的 total_rate 不是一个币种，用户会懵。推荐放在 AgentState 里，第一次设置，后面一路复用。

5. **brands 数组是动态的，不要硬编码**。品牌 `id`（如 33=Accor、223=Archipelago）是本次搜索返回的 `brands` 字段内的 id；**跨时间/跨 q 不保证一致**。正确做法：每次搜索到 brands 后建 `{id: name}` 映射，展示给用户时让用户选「只要 Accor 系」，下一次请求把选中的 id 填入 brands 参数即可。

6. **hotels 专属参数（hotel_class / free_cancellation / brands / special_offers / eco_certified）在 `vacation_rentals=true` 时全部失效**。Vacation Rentals 只认 `bedrooms / bathrooms`；如果用户既想要"民宿"又想要"4 星"，需要引导用户二选一（或两次搜索）。

7. **pagination 翻页：除了 next_page_token 其他参数必须一字不差**。翻第 2 页时如果改了 sort_by 或 min_price，`next_page_token` 会直接失效。如果用户要求"改排序再看更多"，**必须回到首页重搜**，不要带旧 token。

8. **总价优先展示 total_rate，不要展示单晚 × 晚数**。因为单晚 × 晚数通常是税前，total_rate 里有 `before_taxes_fees` 和（含税）`lowest`，更贴近用户实际要付的。字段解读：
   - `total_rate.before_taxes_fees` = 税前总价（不含税、不含度假村费、不含清洁费 VRBO 场景）
   - `total_rate.lowest` = Google 估算含税费总价

9. **两种 kind 不要混排时缺失字段**：`type=hotel` 会有 `hotel_class` / `reviews_breakdown` / `deal_description`；`type=vacation rental` 没有这些，但有 `Kitchen` 等特殊 amenity。展示逻辑里一定要用 dataclass 的 `kind` 字段做分支，不要硬取可能为 None 的字段。

10. **property_token 只能从 Autocomplete 或前一次搜索返回拿**。永远不要自己拼接、不要替换字符、不要用 kgmid/data_cid 顶替。错误的 property_token 会让 Google 返回毫不相干的酒店或空结果。

11. **ads 不要过滤**。很多时候 ads（Booking/Expedia 付费投的）比 organic properties 价格更低或渠道更靠谱，同时它们也带 property_token，可以和 organic 一样进入单店模式。推荐把 ads 和 properties 混合展示给用户时在卡片上放一个小小的 `Ad` 标签或来源 `Booking.com`。

12. **额度**：SerpAPI 免费版 250 次/月，google_hotels 一次调用扣 1 次，翻页另扣 1 次。Travel Assistant 真实场景（用户连续筛选 3 次 × 每次翻 3 页 = 9 次/用户）很快用完，生产环境建议升级到 Hobby / Pro。

13. **缓存默认 1 小时免费**：同参数结果默认缓存 1h，命中不扣额度。不要为了"拿到今天最新价"无脑开 `no_cache=true`，正确做法：相同 q+日期+人数的查询在 1h 内直接复用第一次结果的缓存，只有在用户明确要求"刷新价格"时才开 `no_cache`。

14. **返回为空（properties=[] 且 ads=[]）的常见原因**：
    - min_price 设太高 或 max_price 设太低
    - rating=9（4.5+）+ 太偏的 q
    - 日期过近（<24h 内入住），Google 有时只给极少量房源
    - 城市名拼错或 gl 和 q 对不上（q="北京"，gl=us）
    这时 Agent 应引导用户："这个筛选条件下暂无结果，建议取消最高评分门槛，把预算放宽到 ¥xxx，或换个日期试试。"

15. **amenities / property_types 用 ID 传入，返回时是文本**。请求 `amenities=35`（Free Wi-Fi），返回 amenities[] 里看到的是 `"Free Wi-Fi"` 文本。因此不能把返回的文本再塞回 amenities 参数；如果要复用筛选条件，必须保留原始请求的 ID 列表在 AgentState 里。

---

## 附：字段快速索引（编码时查）

- **判断是不是单店？** → 看请求 `property_token` 是否非空，或返回 `len(properties)==1` 且 `properties[0].name` 高度匹配。
- **拿到总价（含税费）** → `p.total_rate.lowest` / `p.total_rate.lowest`（字符串含货币符号），数值版 `p.total_rate.lowest`。
- **拿到税前总价** → `p.total_rate.before_taxes`（字符串）/ 数值版 `p.total_rate.before_taxes`。
- **拿到星级（int 可比较）** → `p.hotel_class`（仅 type=hotel 有）。
- **判断能不能免费取消？** → ads 字段 `free_cancellation==true`；properties 场看 `amenities` 里有没有 `"Free cancellation"` 或下次 `google_hotels_properties` 单店详情里取。
- **判断离海滩/机场多远** → `p.nearby_places[].transportations[].duration`。
- **下一页还有吗** → 检查 `serpapi_pagination.next_page_token` 是否存在。
