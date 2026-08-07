# SerpAPI Google Flights Search API 参考文档

## 1. 说明

本文档记录 Google Flights Search API（engine: google_flights）的使用方法和返回格式，**用户提供的代码片段和返回 JSON 原封不动保留在第 2、3 节**。

---

## 2. API 调用示例（用户提供，原封不动）

```python
import serpapi

client = serpapi.Client(api_key=os.environ["SERPAPI_KEY"])  # 从 .env 读取，勿硬编码
results = client.search({
  "engine": "google_flights",
  "departure_id": "PEK",
  "arrival_id": "AUS",
  "outbound_date": "2026-08-04",
  "return_date": "2026-08-10",
  "currency": "USD",
  "hl": "en"
})
```

---

## 3. API 返回格式示例（用户提供，原封不动）

```json
{
  ...
  "best_flights": [
    {
      "flights": [
        {
          "departure_airport": {
            "name": "Beijing Capital International Airport",
            "id": "PEK",
            "time": "2023-10-03 15:10"
          },
          "arrival_airport": {
            "name": "Haneda Airport",
            "id": "HND",
            "time": "2023-10-03 19:35"
          },
          "duration": 205,
          "airplane": "Boeing 787",
          "airline": "ANA",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/NH.png",
          "travel_class": "Economy",
          "flight_number": "NH 962",
          "legroom": "31 in",
          "extensions": [
            "Average legroom (31 in)",
            "Wi-Fi for a fee",
            "In-seat power & USB outlets",
            "On-demand video",
            "Carbon emissions estimate: 133 kg"
          ]
        },
        {
          "departure_airport": {
            "name": "Haneda Airport",
            "id": "HND",
            "time": "2023-10-03 21:05"
          },
          "arrival_airport": {
            "name": "Los Angeles International Airport",
            "id": "LAX",
            "time": "2023-10-03 15:10"
          },
          "duration": 605,
          "airplane": "Boeing 787",
          "airline": "ANA",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/NH.png",
          "travel_class": "Economy",
          "flight_number": "NH 126",
          "ticket_also_sold_by": [
            "United"
          ],
          "legroom": "32 in",
          "extensions": [
            "Above average legroom (32 in)",
            "In-seat power & USB outlets",
            "On-demand video",
            "Carbon emissions estimate: 836 kg"
          ],
          "overnight": true
        },
        {
          "departure_airport": {
            "name": "Los Angeles International Airport",
            "id": "LAX",
            "time": "2023-10-03 19:01"
          },
          "arrival_airport": {
            "name": "Austin-Bergstrom International Airport",
            "id": "AUS",
            "time": "2023-10-03 23:59"
          },
          "duration": 178,
          "airplane": "Boeing 737MAX 9 Passenger",
          "airline": "United",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/UA.png",
          "travel_class": "Economy",
          "flight_number": "UA 2175",
          "legroom": "30 in",
          "extensions": [
            "Average legroom (30 in)",
            "Wi-Fi for a fee",
            "In-seat power outlet",
            "Stream media to your device",
            "Carbon emissions estimate: 135 kg"
          ]
        }
      ],
      "layovers": [
        {
          "duration": 90,
          "name": "Haneda Airport",
          "id": "HND"
        },
        {
          "duration": 231,
          "name": "Los Angeles International Airport",
          "id": "LAX"
        }
      ],
      "total_duration": 1309,
      "carbon_emissions": {
        "this_flight": 1106000,
        "typical_for_this_route": 949000,
        "difference_percent": 17
      },
      "price": 2512,
      "type": "Round trip",
      "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/multi.png",
      "departure_token": "W1siUEVLIiwiMjAyMy0xMC0wMyIsIkhORCIsbnVsbCwiTkgiLCI5NjIiXSxbIkhORCIsIjIwMjMtMTAtMDMiLCJMQVgiLG51bGwsIk5IIiwiMTI2Il0sWyJMQVgiLCIyMDIzLTEwLTAzIiwiQVVTIixudWxsLCJVQSIsIjIxNzUiXV0="
    },
    {
      "flights": [
        {
          "departure_airport": {
            "name": "Beijing Capital International Airport",
            "id": "PEK",
            "time": "2023-10-03 10:40"
          },
          "arrival_airport": {
            "name": "Incheon International Airport",
            "id": "ICN",
            "time": "2023-10-03 13:50"
          },
          "duration": 130,
          "airplane": "Airbus A330",
          "airline": "Asiana",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/OZ.png",
          "travel_class": "Economy",
          "flight_number": "OZ 332",
          "legroom": "32 in",
          "extensions": [
            "Above average legroom (32 in)",
            "In-seat power outlet",
            "On-demand video",
            "Carbon emissions estimate: 84 kg"
          ]
        },
        {
          "departure_airport": {
            "name": "Incheon International Airport",
            "id": "ICN",
            "time": "2023-10-03 20:55"
          },
          "arrival_airport": {
            "name": "San Francisco International Airport",
            "id": "SFO",
            "time": "2023-10-03 15:30"
          },
          "duration": 635,
          "airplane": "Airbus A350",
          "airline": "Asiana",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/OZ.png",
          "travel_class": "Economy",
          "flight_number": "OZ 212",
          "legroom": "32 in",
          "extensions": [
            "Above average legroom (32 in)",
            "Wi-Fi for a fee",
            "In-seat power & USB outlets",
            "On-demand video",
            "Carbon emissions estimate: 619 kg"
          ],
          "overnight": true,
          "often_delayed_by_over_30_min": true
        },
        {
          "departure_airport": {
            "name": "San Francisco International Airport",
            "id": "SFO",
            "time": "2023-10-04 07:40"
          },
          "arrival_airport": {
            "name": "Austin-Bergstrom International Airport",
            "id": "AUS",
            "time": "2023-10-04 13:10"
          },
          "duration": 210,
          "airplane": "Boeing 737",
          "airline": "Alaska",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/AS.png",
          "travel_class": "Economy",
          "flight_number": "AS 512",
          "legroom": "31 in",
          "extensions": [
            "Average legroom (31 in)",
            "Wi-Fi for a fee",
            "In-seat power & USB outlets",
            "Stream media to your device",
            "Carbon emissions estimate: 175 kg"
          ]
        }
      ],
      "layovers": [
        {
          "duration": 425,
          "name": "Incheon International Airport",
          "id": "ICN"
        },
        {
          "duration": 970,
          "name": "San Francisco International Airport",
          "id": "SFO",
          "overnight": true
        }
      ],
      "total_duration": 2370,
      "carbon_emissions": {
        "this_flight": 880000,
        "typical_for_this_route": 949000,
        "difference_percent": -7
      },
      "price": 2513,
      "type": "Round trip",
      "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/multi.png",
      "departure_token": "W1siUEVLIiwiMjAyMy0xMC0wMyIsIklDTiIsbnVsbCwiT1oiLCIzMzIiXSxbIklDTiIsIjIwMjMtMTAtMDMiLCJTRk8iLG51bGwsIk9aIiwiMjEyIl0sWyJTRk8iLCIyMDIzLTEwLTA0IiwiQVVTIixudWxsLCJBUyIsIjUxMiJdXQ=="
    },
    ...
  ],
  "other_flights": [
    {
      "flights": [
        {
          "departure_airport": {
            "name": "Beijing Capital International Airport",
            "id": "PEK",
            "time": "2023-10-03 18:30"
          },
          "arrival_airport": {
            "name": "Incheon International Airport",
            "id": "ICN",
            "time": "2023-10-03 21:40"
          },
          "duration": 130,
          "airplane": "Boeing 737MAX 8 Passenger",
          "airline": "Korean Air",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/KE.png",
          "travel_class": "Economy",
          "flight_number": "KE 860",
          "legroom": "31 in",
          "extensions": [
            "Average legroom (31 in)",
            "Wi-Fi for a fee",
            "In-seat power & USB outlets",
            "Stream media to your device",
            "Carbon emissions estimate: 81 kg"
          ]
        },
        {
          "departure_airport": {
            "name": "Incheon International Airport",
            "id": "ICN",
            "time": "2023-10-04 09:20"
          },
          "arrival_airport": {
            "name": "Dallas/Fort Worth International Airport",
            "id": "DFW",
            "time": "2023-10-04 08:00"
          },
          "duration": 760,
          "airplane": "Boeing 787",
          "airline": "Korean Air",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/KE.png",
          "travel_class": "Economy",
          "flight_number": "KE 31",
          "legroom": "33 in",
          "extensions": [
            "Above average legroom (33 in)",
            "In-seat power & USB outlets",
            "On-demand video",
            "Carbon emissions estimate: 807 kg"
          ],
          "overnight": true
        },
        {
          "departure_airport": {
            "name": "Dallas/Fort Worth International Airport",
            "id": "DFW",
            "time": "2023-10-04 09:35"
          },
          "arrival_airport": {
            "name": "Austin-Bergstrom International Airport",
            "id": "AUS",
            "time": "2023-10-04 10:40"
          },
          "duration": 65,
          "airplane": "Embraer 175",
          "airline": "American",
          "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/AA.png",
          "travel_class": "Economy",
          "flight_number": "AA 3489",
          "legroom": "30 in",
          "extensions": [
            "Average legroom (30 in)",
            "Wi-Fi for a fee",
            "In-seat power & USB outlets",
            "Stream media to your device",
            "Carbon emissions estimate: 60 kg"
          ]
        }
      ],
      "layovers": [
        {
          "duration": 700,
          "name": "Incheon International Airport",
          "id": "ICN",
          "overnight": true
        },
        {
          "duration": 95,
          "name": "Dallas/Fort Worth International Airport",
          "id": "DFW"
        }
      ],
      "total_duration": 1750,
      "carbon_emissions": {
        "this_flight": 949000,
        "typical_for_this_route": 949000,
        "difference_percent": 0
      },
      "price": 3521,
      "type": "Round trip",
      "airline_logo": "https://www.gstatic.com/flights/airline_logos/70px/multi.png",
      "departure_token": "W1siUEVLIiwiMjAyMy0xMC0wMyIsIklDTiIsbnVsbCwiS0UiLCI4NjAiXSxbIklDTiIsIjIwMjMtMTAtMDQiLCJERlciLG51bGwsIktFIiwiMzEiXSxbIkRGVyIsIjIwMjMtMTAtMDQiLCJBVVMiLG51bGwsIkFBIiwiMzQ4OSJdXQ=="
    },
    ...
  ],
  ...
}
```

---

## 4. API 请求参数说明

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `engine` | string | ✅ 是 | 固定值：`google_flights` |
| `departure_id` | string | ✅ 是 | 出发机场 IATA 代码，来自 Autocomplete 的 `airports[].id`，如 `"PEK"` |
| `arrival_id` | string | ✅ 是 | 到达机场 IATA 代码，如 `"AUS"` |
| `outbound_date` | string | ✅ 是 | 出发日期，格式 `YYYY-MM-DD`，如 `"2026-08-04"` |
| `return_date` | string | 条件必填 | 返回日期，仅往返行程需要，格式同上 |
| `currency` | string | 推荐 | 货币代码，ISO 4217，如 `"USD"`、`"CNY"`、`"EUR"`，默认可能为 USD |
| `hl` | string | 推荐 | 语言代码，如 `"en"`、`"zh-CN"`，默认英文 |
| `gl` | string | ⚠️ 慎用 | 销售地（point of sale）。**会实打实换掉结果集**，本项目对航班**刻意不传**，见下方实验 |
| `travel_class` | string | 否 | 舱位：`economy` / `premium_economy` / `business` / `first`，默认 economy |
| `adults` | integer | 否 | 成年乘客人数，默认 1 |
| `children` | integer | 否 | 儿童乘客人数，默认 0 |
| `infants_in_seat` | integer | 否 | 占座婴儿数，默认 0 |
| `infants_on_lap` | integer | 否 | 不占座婴儿数，默认 0 |
| `stops` | integer | 否 | 最大中转次数：0=直飞，1=最多1次中转，不填=不限 |
| `max_price` | integer | 否 | 单程/往返最高总价（单位依 currency 而定） |
| `departure_token` | string | 否 | 从搜索结果中获得的 token，用于跟踪特定航班组合 |
| `type` | integer | 否 | ⚠️ **本行原先写反了**。实测（2026-08-05，PEK→TFU）：<br>**`1` = 往返**（必须同时给 `return_date`，返回往返总价）<br>**`2` = 单程**（给了 `return_date` 会 HTTP 400：`` `return_date` should not be set if `type` is not `1` (Round trip) ``） |

### 4.1 `gl`（销售地）的实测对照 — 2026-08-06

同一时刻、同一账号，PEK→CTU 2026-09-05/09-08，往返经济舱，**只差 `gl` 一个参数**：

| | `best_flights` 首选 | 其余 |
|---|---|---|
| **不传 `gl`**（本项目的选择） | ¥4685 `3U 8890` PEK→CTU 直飞 | ¥4685 `3U 8900` / `3U 8894` / `3U 8884`，全是直飞 |
| **`gl=cn`** | ¥4735 `ZH 9157` PEK→**无锡** + `ZH 9547` 无锡→CTU，**两段中转** | ¥6160 `HU 7847` 直飞；`3U 8894` 直飞但 `price=null` |

`gl=cn` 把两段中转排在直飞前面、直飞报价反而高出 30%、还有条目干脆没有价格——
Google 在中国销售地的机票库存覆盖本来就差。

**因此航班链路不跟随 `settings.default_gl`**（酒店链路跟随，那边 `gl=cn` 表现正常）。
由独立开关 `settings.serpapi_flights_gl` 控制，默认空。这是权衡后的默认值，不是遗漏。

> ⚠️ 同一航线不同时刻查到的价格与航班号会变（实测隔一小时从 ¥4685/`3U 8890`
> 变成 ¥1927/`TV 9956`）。复现问题时要以同一时刻的对照实验为准，不能拿历史输出比。

---

## 5. 返回字段解析

顶层核心数组：

| 字段 | 类型 | 说明 |
|------|------|------|
| `best_flights` | FlightItinerary[] | Google 推荐的"最佳"航班组合（通常按价格/时长/中转权衡排序） |
| `other_flights` | FlightItinerary[] | 其他可选航班组合 |

### 5.1 FlightItinerary（航班组合/行程）

`best_flights[*]` / `other_flights[*]` 的结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `flights` | FlightLeg[] | 该行程中的每一段航班（中转则有多段） |
| `layovers` | Layover[] | 中转停留信息（与 flights 段数-1对应） |
| `total_duration` | integer | 行程总时长，**单位：分钟** |
| `carbon_emissions` | CarbonEmissions | 碳排放信息 |
| `price` | integer | 总价格（货币单位由请求 `currency` 决定） |
| `type` | string | 行程类型：`"Round trip"` / `"One way"` |
| `airline_logo` | string | 航司/多航司联合 Logo URL |
| `departure_token` | string | 该行程的唯一 token，可用于后续 API 调用跟踪此组合 |

### 5.2 FlightLeg（单段航班）

`flights[*]` 的结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `departure_airport` | AirportTime | 出发机场+时间 |
| `arrival_airport` | AirportTime | 到达机场+时间 |
| `duration` | integer | 本段飞行时长，**单位：分钟** |
| `airplane` | string | 机型，如 `"Boeing 787"`、`"Airbus A350"` |
| `airline` | string | 承运航司名称，如 `"ANA"`、`"United"` |
| `airline_logo` | string | 航司 Logo URL |
| `travel_class` | string | 舱位等级，如 `"Economy"`、`"Business"` |
| `flight_number` | string | 航班号，如 `"NH 962"` |
| `legroom` | string | 腿部空间，如 `"31 in"` |
| `ticket_also_sold_by` | string[] | 可选，其他售卖此票的航司（代码共享） |
| `overnight` | boolean | 可选，是否跨夜飞行 |
| `often_delayed_by_over_30_min` | boolean | 可选，经常延误 30 分钟以上提示 |
| `extensions` | string[] | 附加信息数组：含腿部空间评价、Wi-Fi、电源、娱乐、碳排放等自由文本 |

### 5.3 AirportTime（机场+时间）

`departure_airport` / `arrival_airport` 的结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 机场全称 |
| `id` | string | IATA 代码（3字母） |
| `time` | string | 当地时间，格式 `"YYYY-MM-DD HH:mm"` |

### 5.4 Layover（中转停留）

`layovers[*]` 的结构：

| 字段 | 类型 | 说明 |
|------|------|------|
| `duration` | integer | 停留时长，**单位：分钟** |
| `name` | string | 中转机场名称 |
| `id` | string | 中转机场 IATA 代码 |
| `overnight` | boolean | 可选，是否跨夜停留 |

### 5.5 CarbonEmissions（碳排放）

| 字段 | 类型 | 说明 |
|------|------|------|
| `this_flight` | integer | 此行程碳排放量（克） |
| `typical_for_this_route` | integer | 此航线典型碳排放量（克） |
| `difference_percent` | integer | 与典型值差异百分比：正数=更高，负数=更低 |

---

## 6. 与 FlightSearchParams 的映射关系

Agent Memory 中的 `FlightSearchParams` 映射到 google_flights API 调用参数：

| FlightSearchParams 字段 | → | google_flights API 参数 |
|--------------------------|---|---------------------------|
| `departure_airport_id`   | → | `departure_id`            |
| `arrival_airport_id`     | → | `arrival_id`              |
| `departure_date`         | → | `outbound_date`           |
| `return_date`            | → | `return_date`（仅当 `is_round_trip=true` 时传入） |
| `is_round_trip`          | → | `type`：true→**1**(往返)；false→**2**(单程)，见上方参数表的实测说明 |
| `passengers`             | → | `adults`（儿童另算，暂默认全为成人） |
| `travel_class`           | → | `travel_class`（小写枚举值） |
| —（固定）                | → | `currency`：建议 `"USD"` 或 `"CNY"`，可在配置中固定 |
| —（固定）                | → | `hl`：建议 `"zh-CN"` 或 `"en"`，可依用户偏好 |

---

## 7. 结果展示建议

对用户展示时，建议每个 `FlightItinerary` 呈现如下信息：

1. **价格 + 总时长**（最突出）：如 `💲 $2,512  ·  总时长 21h 49m · 中转 2 次`
2. **碳排放对比**：如 `碳排放 1,106 kg（比同航线平均高 17%）`
3. **每段航班详情**（依次列出）：
   ```
   段1：PEK → HND  |  NH 962 · ANA · Boeing 787
   15:10 起飞 — 19:35 到达  ·  飞行 3h 25m
   舱位：Economy  ·  腿部空间：31 in
   设施：Wi-Fi(付费) · 电源/USB · 点播视频
   ```
4. **中转信息**（如有）：
   ```
   HND 停留 1h 30m  →  LAX 停留 3h 51m
   ```
5. **操作**：为每个行程分配序号或"选项A/B/C"，供用户选择后进入预订环节。
