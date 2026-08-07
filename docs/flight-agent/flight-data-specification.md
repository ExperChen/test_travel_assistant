# Flight Agent 数据格式规范文档

## 1. 概述

本文档定义 Flight Agent 模块中所有核心数据结构的格式规范，确保 Agent Memory、Tool 输入输出、以及各模块之间的数据交互保持一致。

---

## 2. 核心数据结构

### 2.1 Airport（机场信息）

表示单个机场的完整信息，来自 Autocomplete API 返回的 `airports[]` 元素。

**TypeScript 定义**：
```typescript
interface Airport {
  name: string;          // 机场全称，如 "John F. Kennedy International Airport"
  id: string;            // IATA 代码（3字母），如 "JFK" —— 核心标识
  city: string;          // 所在城市名，如 "New York"
  city_id: string;       // 城市知识图谱 ID，如 "/m/02_286"
  distance: string;      // 距市中心距离，如 "14 mi"
}
```

**JSON 示例**：
```json
{
  "name": "John F. Kennedy International Airport",
  "id": "JFK",
  "city": "New York",
  "city_id": "/m/02_286",
  "distance": "14 mi"
}
```

---

### 2.2 CitySuggestion（城市建议条目）

Autocomplete API 返回的 `suggestions[]` 中的每一项，包含一个城市及其下属机场列表。

**TypeScript 定义**：
```typescript
interface CitySuggestion {
  type?: string;               // 类型，如 "City"
  name: string;                // 城市名，如 "New York"
  description?: string;        // 城市描述，如 "City in New York, United States"
  processed_atn?: string;      // 备用描述字段（用户示例中的字段）
  id: string;                  // 城市知识图谱 ID，如 "/m/02_286"
  airports: Airport[];         // 该城市下属的机场列表
}
```

**JSON 示例**：
```json
{
  "type": "City",
  "name": "New York",
  "description": "City in New York, United States",
  "id": "/m/02_286",
  "airports": [
    {
      "name": "John F. Kennedy International Airport",
      "id": "JFK",
      "city": "New York",
      "city_id": "/m/02_286",
      "distance": "14 mi"
    },
    {
      "name": "LaGuardia Airport",
      "id": "LGA",
      "city": "New York",
      "city_id": "/m/02_286",
      "distance": "9 mi"
    }
  ]
}
```

---

### 2.3 AutocompleteRequest（Autocomplete API 请求参数）

**TypeScript 定义**：
```typescript
interface AutocompleteRequest {
  engine: "google_flights_autocomplete";  // 固定值
  q: string;                              // 查询关键词
}
```

**Python 调用参数**：
```python
params = {
    "engine": "google_flights_autocomplete",
    "q": "New York"
}
```

---

### 2.4 AutocompleteResponse（Autocomplete API 响应结构）

**TypeScript 定义**：
```typescript
interface SearchMetadata {
  id: string;
  status: "Success" | "Error";
  json_endpoint?: string;
  created_at?: string;
  processed_at?: string;
}

interface AutocompleteResponse {
  search_metadata: SearchMetadata;
  suggestions: CitySuggestion[];
}
```

---

### 2.5 FlightSearchParams（航班搜索参数集合）

Agent Memory 中存储的核心参数对象，也是后续调用航班搜索 API 的输入。

**TypeScript 定义**：
```typescript
type TravelClass = "economy" | "premium_economy" | "business" | "first";

interface FlightSearchParams {
  // === 必填字段 ===
  departure_airport_id: string | null;   // 出发机场 IATA 代码，如 "JFK"
  arrival_airport_id: string | null;     // 到达机场 IATA 代码，如 "HND"
  departure_date: string | null;         // 出发日期，格式 "YYYY-MM-DD"
  is_round_trip: boolean | null;         // 是否往返
  passengers: number;                    // 乘客人数，默认 1

  // === 条件必填 ===
  return_date: string | null;            // 返回日期，仅往返时需要

  // === 可选字段 ===
  travel_class?: TravelClass | null;     // 舱位偏好，不填则全部

  // === 辅助展示字段 ===
  departure_airport?: Airport | null;    // 出发机场完整信息（用于展示）
  arrival_airport?: Airport | null;      // 到达机场完整信息（用于展示）
}
```

**初始化默认值**：
```json
{
  "departure_airport_id": null,
  "arrival_airport_id": null,
  "departure_date": null,
  "is_round_trip": null,
  "passengers": 1,
  "return_date": null,
  "travel_class": null,
  "departure_airport": null,
  "arrival_airport": null
}
```

**完整填充示例**：
```json
{
  "departure_airport_id": "JFK",
  "arrival_airport_id": "HND",
  "departure_date": "2026-08-10",
  "is_round_trip": true,
  "passengers": 1,
  "return_date": "2026-08-16",
  "travel_class": "economy",
  "departure_airport": {
    "name": "John F. Kennedy International Airport",
    "id": "JFK",
    "city": "New York",
    "city_id": "/m/02_286",
    "distance": "14 mi"
  },
  "arrival_airport": {
    "name": "Haneda Airport",
    "id": "HND",
    "city": "Tokyo",
    "city_id": "/m/03_3d",
    "distance": "9 mi"
  }
}
```

---

### 2.6 AgentState（Agent 运行时状态）

Agent 在多轮对话中的完整状态对象，用于 Memory 持久化。

**TypeScript 定义**：
```typescript
type ConversationPhase =
  | "collecting_departure"      // 收集出发机场
  | "awaiting_departure_select" // 已展示出发机场列表，等待用户选择
  | "collecting_arrival"        // 收集到达机场
  | "awaiting_arrival_select"   // 已展示到达机场列表，等待用户选择
  | "collecting_dates"          // 收集日期
  | "collecting_passengers"     // 收集乘客信息
  | "collecting_class"          // 收集舱位偏好
  | "awaiting_confirmation"     // 已展示参数汇总，等待用户确认
  | "ready_to_search"           // 参数确认完毕，可调用搜索API
  | "search_completed"          // 航班搜索结果已返回，展示给用户
  | "booking";                  // 用户选定行程，进入预订流程（后续）

interface AgentState {
  phase: ConversationPhase;
  search_params: FlightSearchParams;
  last_query?: {
    type: "departure" | "arrival";
    q: string;
    results: CitySuggestion[];
  } | null;   // 上一次 Autocomplete 的结果，用于匹配用户选择的序号
  search_results?: FlightSearchResults | null;  // 航班搜索结果（Search API 返回后填充）
  message_history: Message[];
}
```

---

### 2.7 Message（对话消息）

**TypeScript 定义**：
```typescript
type MessageRole = "user" | "assistant" | "system";

interface Message {
  role: MessageRole;
  content: string;
  timestamp: string;  // ISO 8601 格式，如 "2026-08-04T15:30:00Z"
}
```

---

### 2.8 GoogleFlightsSearchRequest（航班搜索 API 请求参数）

google_flights engine 的调用参数，由 FlightSearchParams 转换而来。

**TypeScript 定义**：
```typescript
interface GoogleFlightsSearchRequest {
  engine: "google_flights";           // 固定值
  departure_id: string;               // 出发机场 IATA，如 "PEK"
  arrival_id: string;                 // 到达机场 IATA，如 "AUS"
  outbound_date: string;              // 出发日期 YYYY-MM-DD
  return_date?: string;               // 返回日期（仅往返）
  currency?: string;                  // ISO 货币代码，推荐 "USD" / "CNY"
  hl?: string;                        // 语言，推荐 "zh-CN" / "en"
  travel_class?: TravelClass;         // 舱位，默认 economy
  adults?: number;                    // 成人乘客数，默认 1
  children?: number;                  // 儿童数，默认 0
  stops?: number;                     // 最大中转：0=直飞，1=1次，不填=不限
  max_price?: number;                 // 总价上限
  type?: 1 | 2;                       // 1=单程，2=往返(默认)
}
```

**Python 调用参数示例（由用户原始示例）**：
```python
params = {
    "engine": "google_flights",
    "departure_id": "PEK",
    "arrival_id": "AUS",
    "outbound_date": "2026-08-04",
    "return_date": "2026-08-10",
    "currency": "USD",
    "hl": "en"
}
```

---

### 2.9 Google Flights Search 响应核心数据结构

#### 2.9.1 AirportTime（带时间的机场）

单段航班的出发/到达机场 + 当地时间。

```typescript
interface AirportTime {
  name: string;   // 机场全称
  id: string;     // IATA 代码
  time: string;   // 当地时间 "YYYY-MM-DD HH:mm"
}
```

#### 2.9.2 FlightLeg（单段航班航段）

```typescript
interface FlightLeg {
  departure_airport: AirportTime;
  arrival_airport: AirportTime;
  duration: number;                  // 本段飞行时长（分钟）
  airplane: string;                  // 机型，如 "Boeing 787"
  airline: string;                   // 航司名，如 "ANA"
  airline_logo: string;              // Logo URL
  travel_class: string;              // 舱位，如 "Economy"
  flight_number: string;             // 航班号，如 "NH 962"
  legroom: string;                   // 腿部空间，如 "31 in"
  ticket_also_sold_by?: string[];    // 代码共享的其他航司
  overnight?: boolean;               // 是否跨夜飞行
  often_delayed_by_over_30_min?: boolean;  // 常延误 30+ 分钟警告
  extensions: string[];              // 附加信息（Wi-Fi、电源等自由文本）
}
```

#### 2.9.3 Layover（中转停留）

```typescript
interface Layover {
  duration: number;   // 停留时长（分钟）
  name: string;       // 机场名
  id: string;         // IATA 代码
  overnight?: boolean; // 是否跨夜停留
}
```

#### 2.9.4 CarbonEmissions（碳排放）

```typescript
interface CarbonEmissions {
  this_flight: number;           // 本次排放量（克）
  typical_for_this_route: number;// 同航线典型排放（克）
  difference_percent: number;    // 差异百分比，可正可负
}
```

#### 2.9.5 FlightItinerary（完整航班组合/行程）

best_flights / other_flights 数组的元素。

```typescript
interface FlightItinerary {
  flights: FlightLeg[];              // 所有航段（中转 = 多段）
  layovers: Layover[];               // 中转停留信息
  total_duration: number;            // 全程总时长（分钟）
  carbon_emissions: CarbonEmissions; // 碳排放
  price: number;                     // 总价格（currency 指定货币）
  type: string;                      // "Round trip" / "One way"
  airline_logo: string;              // 主/多航司联合 Logo URL
  departure_token: string;           // 该行程唯一 token，后续追踪/比价用
}
```

#### 2.9.6 FlightSearchResults（完整搜索响应核心）

```typescript
interface FlightSearchResults {
  best_flights: FlightItinerary[];   // Google 推荐最佳组合
  other_flights: FlightItinerary[];  // 其他可选组合
  search_metadata?: SearchMetadata;  // 通用元数据（同 Autocomplete）
}
```

---

## 3. 数据流转规范

### 3.1 机场选择确认流程中的数据传递

```
用户输入: "从纽约出发"
    │
    ▼
Agent 判断需要出发机场，调用 Autocomplete(q="纽约")
    │
    ▼
API 返回 CitySuggestion[]
    │
    ▼
存入 AgentState.last_query = { type: "departure", q: "纽约", results: [...] }
    │
    ▼
展示带序号的机场列表给用户
    │
    ▼
用户输入: "1" 或 "JFK"
    │
    ▼
根据 last_query.results 匹配对应的 Airport 对象
    │
    ▼
更新 search_params:
  - departure_airport_id = airport.id ("JFK")
  - departure_airport = airport (完整对象)
    │
    ▼
清空 last_query，进入下一阶段 (collecting_arrival)
```

### 3.2 用户选择机场时的匹配规则

用户可以通过以下两种方式确认机场，匹配优先级：

| 用户输入类型 | 匹配逻辑 | 示例 |
|-------------|----------|------|
| **数字序号** | 匹配 `last_query.results[].airports[]` 展开后的第 N-1 项 | 用户输入 "2" → 取展示列表的第2项 |
| **IATA 代码** | 在 `last_query.results[].airports[].id` 中精确匹配（忽略大小写） | 用户输入 "JFK" 或 "jfk" → 匹配 id="JFK" |

---

## 4. 日期格式规范

### 4.1 存储格式
- **必须使用** ISO 8601 日期格式：`YYYY-MM-DD`
- 示例：`2026-08-10`

### 4.2 展示格式
向用户展示时，建议格式：
- `2026-08-10（周一）`
- 或根据用户本地化习惯：`2026年8月10日 星期一`

### 4.3 模糊日期解析表

| 用户表达 | 今天基准（2026-08-04 周二） | 解析结果 |
|----------|------------------------------|----------|
| 今天 | - | 2026-08-04 |
| 明天 | - | 2026-08-05 |
| 后天 | - | 2026-08-06 |
| 周一 / 本周一 | - | 2026-08-10 |
| 下周一 | - | 2026-08-10 |
| 下周日 | - | 2026-08-09 或 2026-08-16（需根据上下文判断，建议询问确认） |
| 8月10号 / 8/10 | - | 2026-08-10 |
| 8月 | - | 提示用户给出具体日期，或询问是8月初/中旬/下旬 |

---

## 5. 参数完成度检查

判断是否可以进入"参数确认展示"阶段，需要满足以下条件：

```typescript
function isParamsReady(params: FlightSearchParams): boolean {
  // 必填项
  if (!params.departure_airport_id) return false;
  if (!params.arrival_airport_id) return false;
  if (!params.departure_date) return false;
  if (params.is_round_trip === null) return false;
  if (!params.passengers || params.passengers < 1) return false;

  // 往返必须有返回日期
  if (params.is_round_trip && !params.return_date) return false;

  // 出发日期不能晚于返回日期
  if (params.is_round_trip && params.return_date) {
    if (new Date(params.departure_date) > new Date(params.return_date)) {
      return false;
    }
  }

  return true;
}
```

---

## 6. 错误数据格式

### 6.1 API 错误响应

```typescript
interface ApiError {
  code: string;           // 错误码，如 "TIMEOUT", "INVALID_KEY", "NO_RESULTS"
  message: string;        // 技术错误信息（日志用）
  userMessage: string;    // 展示给用户的友好提示
}
```

**示例**：
```json
{
  "code": "NO_RESULTS",
  "message": "Autocomplete API returned empty suggestions array for q='XyzUnknownCity'",
  "userMessage": "抱歉，没有找到匹配的机场，请尝试输入其他城市名或机场关键词。"
}
```

---

## 7. 枚举值速查表

### 7.1 TravelClass 舱位代码映射

| 枚举值 | 中文名称 | 英文名称 |
|--------|----------|----------|
| `economy` | 经济舱 | Economy |
| `premium_economy` | 超级经济舱 | Premium Economy |
| `business` | 商务舱 | Business |
| `first` | 头等舱 | First |

### 7.2 ConversationPhase 含义说明

| 阶段值 | 触发条件 | Agent 行为 |
|--------|----------|------------|
| `collecting_departure` | 初始状态或上次出发机场被清空 | 向用户询问出发城市 |
| `awaiting_departure_select` | 已调用 Autocomplete 并展示出发机场列表 | 等待用户通过序号/IATA代码选择 |
| `collecting_arrival` | 出发机场已确认 | 向用户询问目的城市 |
| `awaiting_arrival_select` | 已展示到达机场列表 | 等待用户确认到达机场 |
| `collecting_dates` | 两端机场均确认 | 询问出发日期，并判断是否往返 |
| `collecting_passengers` | 日期收集完毕 | 询问乘客人数（可默认1） |
| `collecting_class` | 乘客人数确认 | 询问舱位偏好（可选） |
| `awaiting_confirmation` | 所有必填参数收集完毕 | 展示汇总表格等待用户最终确认 |
| `ready_to_search` | 用户点击确认 | 调用 google_flights Search API |
| `search_completed` | 搜索结果返回 | 展示 best_flights / other_flights，等待用户选择行程 |
| `booking` | 用户选定某个行程 | 进入预订流程（后续实现） |
