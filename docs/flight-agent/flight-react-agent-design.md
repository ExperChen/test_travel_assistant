# Flight ReAct Agent 设计与交互流程文档

## 1. ReAct Agent 概述

Flight Agent 采用 **ReAct (Reasoning + Acting)** 模式，是一个"小ReAct Agent"，专注于航班搜索流程中的机场选择环节。Agent 会在推理（Thought）和行动（Action/ Observation）之间循环，直到收集到足够的信息或完成任务。

## 2. Agent 核心循环

```
用户输入
    │
    ▼
┌───────────────────────────────────┐
│  Thought (推理)                    │
│  - 分析当前对话状态                 │
│  - 判断缺少哪些参数                │
│  - 决定下一步动作（提问/调用工具）  │
└─────────────┬─────────────────────┘
              │
       ┌──────┴───────┐
       ▼              ▼
   提问用户      调用 Tool
       │              │
       │              ▼
       │      ┌──────────────────┐
       │      │   Action         │
       │      │  (执行工具调用)   │
       │      └────────┬─────────┘
       │               ▼
       │      ┌──────────────────┐
       │      │   Observation    │
       │      │  (获取工具结果)   │
       │      └────────┬─────────┘
       │               │
       └───────┬───────┘
               ▼
        生成 Agent 响应
               │
               ▼
        检查是否完成？─────是─────► 结束
               │否
               ▼
          等待用户输入 ◄──────────┘
```

## 3. Tool 定义

### 3.1 Tool 1: google_flights_autocomplete

**用途**：根据用户输入的关键词（城市名/机场名），调用 SerpAPI 获取机场建议列表。

**Tool Schema**：
```json
{
  "name": "google_flights_autocomplete",
  "description": "当用户提供出发地或目的地城市名/关键词时，调用此API获取匹配的机场列表，返回机场ID(IATA代码)、名称、城市、距离等信息。输入参数为用户提供的查询字符串q。",
  "parameters": {
    "type": "object",
    "properties": {
      "q": {
        "type": "string",
        "description": "用户输入的查询关键词，如城市名、机场名的部分或全部，例如 'New'、'上海'、'Tokyo'、'JFK'"
      }
    },
    "required": ["q"]
  }
}
```

**调用示例（Action）**：
```
Action: google_flights_autocomplete
Action Input: {"q": "New"}
```

**返回处理（Observation）**：
- 解析 `suggestions` 数组，提取所有城市及其下属 `airports`
- 按城市分组展示机场列表，为每个机场分配一个序号
- 等待用户通过序号或机场ID（IATA代码）确认选择

---

### 3.2 Tool 2: google_flights_search

**用途**：当所有航班搜索参数（出发/到达机场、日期、乘客、舱位等）收集完毕且用户确认后，调用此 API 搜索可用航班，返回 `best_flights`（推荐最佳）和 `other_flights`（其他）两组行程。

**Tool Schema**：
```json
{
  "name": "google_flights_search",
  "description": "当用户已确认所有航班搜索参数（出发机场IATA、到达机场IATA、出发日期、往返标志、乘客数、舱位等）后，调用此API获取航班组合列表，返回 best_flights（推荐）和 other_flights（其他）。每个行程包含 flights（各段）、layovers（中转）、price（价格）、total_duration（总时长分钟）、carbon_emissions（碳排放）、departure_token（行程唯一标识）等字段。",
  "parameters": {
    "type": "object",
    "properties": {
      "departure_id":     { "type": "string", "description": "出发机场 IATA 代码，如 JFK / PEK" },
      "arrival_id":       { "type": "string", "description": "到达机场 IATA 代码，如 HND / AUS" },
      "outbound_date":    { "type": "string", "description": "出发日期，格式 YYYY-MM-DD" },
      "return_date":      { "type": "string", "description": "返回日期，格式 YYYY-MM-DD，仅往返行程需要" },
      "is_round_trip":    { "type": "boolean", "description": "是否往返，true 时必须同时提供 return_date" },
      "passengers":       { "type": "integer", "description": "乘客人数（成人），默认 1" },
      "travel_class":     { "type": "string", "enum": ["economy","premium_economy","business","first"], "description": "舱位偏好，默认 economy" },
      "currency":         { "type": "string", "description": "货币 ISO 代码，推荐 USD 或 CNY，默认 USD" },
      "hl":               { "type": "string", "description": "语言代码，推荐 zh-CN 或 en，默认 en" }
    },
    "required": ["departure_id","arrival_id","outbound_date","is_round_trip","passengers"]
  }
}
```

**调用示例（Action）**：
```
Action: google_flights_search
Action Input: {"departure_id":"JFK","arrival_id":"HND","outbound_date":"2026-08-10","return_date":"2026-08-16","is_round_trip":true,"passengers":1,"travel_class":"economy","currency":"USD","hl":"en"}
```

**返回处理（Observation）**：
- 解析 `best_flights` 和 `other_flights`
- 为每个 `FlightItinerary` 分配一个序号（如"方案1/方案2..."）
- 向用户展示每个方案的：价格、总时长、中转次数、每段航班详情、中转信息、碳排放对比
- 等待用户选择某个方案序号进入预订阶段

---

## 4. 参数收集清单

Agent 需要按顺序收集以下参数，直到全部齐全：

| 序号 | 参数名 | 类型 | 必填 | 说明 |
|------|--------|------|------|------|
| 1 | `departure_airport_id` | string | 是 | 出发机场 IATA 代码（来自 airports[].id） |
| 2 | `arrival_airport_id` | string | 是 | 到达机场 IATA 代码 |
| 3 | `departure_date` | string | 是 | 出发日期，格式 `YYYY-MM-DD` |
| 4 | `is_round_trip` | boolean | 是 | 是否往返 |
| 5 | `return_date` | string | 条件必填 | 返回日期，仅往返时需要 |
| 6 | `passengers` | integer | 是 | 乘客人数，默认 1 |
| 7 | `travel_class` | string | 否 | 舱位偏好：economy / premium_economy / business / first |

## 5. 详细交互流程示例

### 场景：用户预订从纽约到东京的往返机票

---

#### Turn 1：用户初始请求

**User**：
> 我想订一张从纽约到东京的往返机票，下周出发

**Agent Thought**：
用户想要订往返机票，提到了出发地纽约和目的地东京，以及下周出发。但缺少：
1. 具体的出发机场（纽约有多个机场）
2. 具体的到达机场（东京有多个机场）
3. 具体的出发日期（"下周"太模糊）
4. 返回日期

根据优先级，应该先确认出发机场。我需要先调用 Autocomplete API 获取纽约的所有机场。

**Action**：
```
Action: google_flights_autocomplete
Action Input: {"q": "New York"}
```

**Observation**：
```json
{
  "suggestions": [
    {
      "name": "New York",
      "description": "City in New York, United States",
      "id": "/m/02_286",
      "airports": [
        {
          "name": "John F. Kennedy International Airport",
          "id": "JFK",
          "city": "New York",
          "distance": "14 mi"
        },
        {
          "name": "Newark Liberty International Airport",
          "id": "EWR",
          "city": "Newark",
          "distance": "9 mi"
        },
        {
          "name": "LaGuardia Airport",
          "id": "LGA",
          "city": "New York",
          "distance": "9 mi"
        }
      ]
    }
  ]
}
```

**Agent Response**：
> 纽约地区有以下机场可供选择，请告诉我您想从哪个机场出发（序号或机场ID均可）：
>
> **出发地 - 纽约 (New York)**
> 1. **[JFK]** John F. Kennedy International Airport - 距市中心 14 mi
> 2. **[EWR]** Newark Liberty International Airport - 距市中心 9 mi（位于纽瓦克）
> 3. **[LGA]** LaGuardia Airport - 距市中心 9 mi

---

#### Turn 2：用户确认出发机场

**User**：
> 从JFK出发

**Agent Thought**：
用户选择了 JFK 作为出发机场（departure_airport_id = "JFK"）。接下来需要确认目的地东京的机场。继续调用 Autocomplete API。

**Action**：
```
Action: google_flights_autocomplete
Action Input: {"q": "Tokyo"}
```

**Observation**：
```json
{
  "suggestions": [
    {
      "name": "Tokyo",
      "description": "Capital of Japan",
      "id": "/m/03_3d",
      "airports": [
        {
          "name": "Haneda Airport",
          "id": "HND",
          "city": "Tokyo",
          "distance": "9 mi"
        },
        {
          "name": "Narita International Airport",
          "id": "NRT",
          "city": "Narita",
          "distance": "40 mi"
        }
      ]
    }
  ]
}
```

**Agent Response**：
> 好的，已确认出发机场：**JFK (John F. Kennedy International Airport)**
>
> 东京地区有以下机场可供选择，请选择目的地机场：
>
> **目的地 - 东京 (Tokyo)**
> 1. **[HND]** Haneda Airport - 距市中心 9 mi
> 2. **[NRT]** Narita International Airport - 距市中心 40 mi（位于成田市）

---

#### Turn 3：用户确认到达机场并提供日期

**User**：
> 到羽田机场，下周一出发，下周日回来

**Agent Thought**：
用户确认了到达机场 HND（Haneda Airport），并提供了出发日期"下周一"和返回日期"下周日"。
今天是 2026-08-04（周二），因此：
- 下周一 = 2026-08-10
- 下周日 = 2026-08-16

已收集参数：
- departure_airport_id = "JFK" ✓
- arrival_airport_id = "HND" ✓
- departure_date = "2026-08-10" ✓
- is_round_trip = true ✓
- return_date = "2026-08-16" ✓

还需要确认乘客人数和舱位偏好。先询问并给出默认值。

**Agent Response**：
> 好的，以下是已确认的信息：
>
> | 项目 | 内容 |
> |------|------|
> | 出发机场 | JFK - John F. Kennedy International Airport |
> | 到达机场 | HND - Haneda Airport |
> | 出发日期 | 2026-08-10（下周一） |
> | 行程类型 | 往返 |
> | 返回日期 | 2026-08-16（下周日） |
>
> 还需要确认以下信息：
> - **乘客人数**：默认为 1 位成人，需要调整吗？
> - **舱位偏好**：经济舱 / 超级经济舱 / 商务舱 / 头等舱？不选的话默认搜索全部舱位。

---

#### Turn 4：用户补充信息并确认

**User**：
> 1个人，经济舱就可以

**Agent Thought**：
用户确认了乘客人数=1，舱位=经济舱（economy）。所有必要参数已收集完毕。
现在展示完整的参数汇总，让用户确认后再调用航班搜索API。

**Agent Response**：
> ✅ 所有信息已收集完毕，以下是您的航班搜索参数汇总：
>
> | 参数 | 值 |
> |------|-----|
> | 出发机场 | JFK (John F. Kennedy International Airport) |
> | 到达机场 | HND (Haneda Airport) |
> | 出发日期 | 2026-08-10 |
> | 返回日期 | 2026-08-16 |
> | 乘客 | 1 人 |
> | 舱位 | 经济舱 (Economy) |
>
> 请确认以上信息是否正确？确认后我将为您搜索航班。

---

#### Turn 5：用户确认，调用搜索 API

**User**：
> 确认，开始搜索

**Agent Thought**：
用户已确认所有参数，满足调用搜索 API 的条件。现在调用 google_flights_search，传入：
- departure_id: "JFK"
- arrival_id: "HND"
- outbound_date: "2026-08-10"
- return_date: "2026-08-16"
- is_round_trip: true
- passengers: 1
- travel_class: "economy"
- currency: "USD"
- hl: "en"

**Action**：
```
Action: google_flights_search
Action Input: {"departure_id":"JFK","arrival_id":"HND","outbound_date":"2026-08-10","return_date":"2026-08-16","is_round_trip":true,"passengers":1,"travel_class":"economy","currency":"USD","hl":"en"}
```

**Observation**（模拟 API 返回的 best_flights 节选）：
```json
{
  "best_flights": [
    {
      "flights": [
        {
          "departure_airport": { "name": "John F. Kennedy International Airport", "id": "JFK", "time": "2026-08-10 08:30" },
          "arrival_airport":   { "name": "Haneda Airport", "id": "HND", "time": "2026-08-11 12:45" },
          "duration": 855, "airplane": "Boeing 777", "airline": "ANA", "airline_logo": "...NH.png",
          "travel_class": "Economy", "flight_number": "NH 107", "legroom": "32 in",
          "overnight": true,
          "extensions": ["Above average legroom (32 in)","Wi-Fi for a fee","In-seat power & USB outlets","On-demand video","Carbon emissions estimate: 902 kg"]
        }
      ],
      "layovers": [],
      "total_duration": 855,
      "carbon_emissions": { "this_flight": 902000, "typical_for_this_route": 950000, "difference_percent": -5 },
      "price": 1820,
      "type": "Round trip",
      "airline_logo": "...NH.png",
      "departure_token": "abc123=="
    },
    {
      "flights": [
        { "departure_airport": { "name": "JFK", "id": "JFK", "time": "2026-08-10 10:15" },
          "arrival_airport":   { "name": "SFO", "id": "SFO", "time": "2026-08-10 13:35" },
          "duration": 320, "airplane": "Airbus A321", "airline": "United", "travel_class": "Economy",
          "flight_number": "UA 456", "legroom": "31 in", "extensions": ["Average legroom (31 in)","Wi-Fi for a fee","Carbon emissions estimate: 210 kg"] },
        { "departure_airport": { "name": "SFO", "id": "SFO", "time": "2026-08-10 17:00" },
          "arrival_airport":   { "name": "HND", "id": "HND", "time": "2026-08-11 21:30" },
          "duration": 690, "airplane": "Boeing 787", "airline": "United", "travel_class": "Economy",
          "flight_number": "UA 875", "legroom": "31 in", "overnight": true,
          "extensions": ["Average legroom (31 in)","On-demand video","Carbon emissions estimate: 688 kg"] }
      ],
      "layovers": [ { "duration": 205, "name": "San Francisco International Airport", "id": "SFO" } ],
      "total_duration": 1215,
      "carbon_emissions": { "this_flight": 898000, "typical_for_this_route": 950000, "difference_percent": -5 },
      "price": 1560,
      "type": "Round trip",
      "airline_logo": "...UA.png",
      "departure_token": "def456=="
    }
  ],
  "other_flights": [ /* ... */ ]
}
```

**Agent Response**：
> 🔍 已为您搜索到 **JFK → HND（往返）** 的航班，以下是推荐方案（按性价比排序）：
>
> ---
> #### 🏆 方案 1（推荐 · 直飞）· **$1,820** · 总时长 14h 15m · 无中转
> - **碳排放**：902 kg（比同航线平均低 5% ✅）
> - **段 1**：NH 107 · ANA · Boeing 777
>   - JFK 08:30 ➜ HND 12:45+1  ·  飞行 14h 15m · 跨夜 🌙
>   - 舱位：Economy · 腿部空间 32 in · 高于平均 ✅
>   - 设施：付费 Wi-Fi · 电源/USB · 点播视频
>
> ---
> #### 💸 方案 2（更便宜）· **$1,560** · 总时长 20h 15m · 中转 1 次
> - **中转**：SFO（旧金山）停留 3h 25m
> - **碳排放**：898 kg（比同航线平均低 5% ✅）
> - **段 1**：UA 456 · United · Airbus A321
>   - JFK 10:15 ➜ SFO 13:35  ·  飞行 5h 20m
>   - 腿部空间 31 in · 付费 Wi-Fi
> - **段 2**：UA 875 · United · Boeing 787
>   - SFO 17:00 ➜ HND 21:30+1  ·  飞行 11h 30m · 跨夜 🌙
>   - 腿部空间 31 in · 点播视频
>
> ---
> 👉 请告诉我您想要选择哪个方案（输入"方案1"或"方案2"），我将为您进入预订流程。  
> 📌 如需查看更多可选方案（other_flights），也可以回复"更多"。

---

## 6. Agent 决策逻辑（Prompt 设计要点）

### 6.1 System Prompt 核心要素

```
你是一个专业的 Flight Booking Agent，帮助用户预订机票。请严格遵循 ReAct 格式进行推理和行动。

## 可用工具
1. google_flights_autocomplete(q) - 根据城市/机场关键词返回机场列表，参数：{q: string}
2. google_flights_search(...) - 搜索航班组合，参数：{departure_id, arrival_id, outbound_date, return_date?, is_round_trip, passengers, travel_class?, currency?, hl?}

## 工作流程
1. 从用户输入中提取出发地、目的地、日期等信息
2. 当出发地或目的地只提到城市名（没有具体机场ID/IATA），必须调用 google_flights_autocomplete
3. 收到 Autocomplete 返回的机场列表后，按"序号 + [IATA] 机场名 - 距离"格式展示给用户选择
4. 用户通过序号或 IATA 代码确认后，将该机场ID存入对应字段（departure_id / arrival_id）并保留完整 Airport 对象
5. 出发机场确认 → 收集目的地机场 → 收集日期（同时判断是否往返）→ 乘客 → 舱位
6. 所有参数收集完毕后，展示汇总表格请用户最终确认
7. 用户确认"开始搜索"后：调用 google_flights_search，传入对应参数
8. 收到搜索结果后：先展示 best_flights 前 3 条（价格+总时长+中转+每段详情+碳排放），并为每个方案分配序号
9. 用户选择方案后 → 进入预订流程（后续实现）

## 机场展示格式
**[城市名]**
N. [IATA代码] 机场名称 - 距市中心 XX mi

## 航班方案展示格式
- 价格 & 总时长最突出
- 中转次数/停留时间单独列
- 碳排放附对比（高于平均用红色⚠，低于用绿色✅）
- 每段航班用"航班号 · 航司 · 机型"开头，然后出发/到达时间、飞行时长、舱位、腿部空间、设施

## 回复规则
- 每次只推进一个核心问题（如先确认出发机场，再确认到达机场，再确认日期...）
- 日期必须转换为 YYYY-MM-DD 格式，并在括号中注明周几
- 永远不要编造机场或航班信息，只能使用 API 返回的结果
- 搜索结果为空时：向用户致歉并建议调整日期/机场/舱位后重试
```

### 6.2 ReAct 输出格式约束

每一步推理必须严格遵循以下格式：

```
Thought: <推理过程>
Action: <工具名，或"Finish"表示结束>
Action Input: <JSON格式参数>
```

或当直接回复用户无需调用工具时：
```
Thought: <推理过程，说明为什么不需要调用工具>
Response: <给用户的自然语言回复>
```

## 7. 边界情况处理

| 阶段 | 场景 | 处理策略 |
|------|------|----------|
| Autocomplete | 用户输入的城市返回 0 条结果 | 提示用户未找到匹配机场，请换个关键词重试 |
| Autocomplete | 用户提供的机场ID不在展示列表中 | 提示用户请从列表中选择，或重新输入正确的IATA代码 |
| 参数收集 | 用户一次性提供了所有信息（如"JFK到HND 8月10日出发8月16日返回"） | 跳过Autocomplete步骤，直接解析参数并展示汇总 |
| 参数收集 | 用户改变主意（如"算了，我还是从LGA出发"） | 更新对应参数，必要时重新展示汇总 |
| Autocomplete | API 调用失败 | 友好提示"抱歉，机场查询服务暂时不可用，请稍后重试"，并记录错误日志 |
| 参数收集 | 用户提供模糊日期（如"下个月初"） | 给出日期范围选项请用户确认，或询问具体日期 |
| Search API | `best_flights` + `other_flights` 全部为空 | 致歉并建议：调整日期±3天、更换附近机场、放宽舱位、允许多一次中转 |
| Search API | 返回结果中 `price` 为 null / 0 | 标注为"价格暂无"，并提示用户点击查看实时价格 |
| Search API | 选中行程后再次查询价格变化 | 告知用户"由于实时定价，当前价格已变更为 $XXX，是否继续预订？" |
| Search API | API 调用超时或限流 | 提示"航班搜索响应较慢，正在为您重试第 N 次..."，最多 3 次后失败 |
| Search API | 用户回复"更多"想看 other_flights | 展示下一批（每次 3 条），并标注"更多方案 N/总数" |
| 搜索结果 | 用户问"哪个最便宜/最快/最少中转" | 从 best_flights+other_flights 中分别挑出对应排序 Top1 推荐 |
| 搜索结果 | 用户输入的选择序号不存在 | 提示"请输入已展示的方案序号，或回复'更多'查看其他方案" |
