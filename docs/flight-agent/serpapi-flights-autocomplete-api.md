# SerpAPI Google Flights Autocomplete API 参考文档

## 1. 说明

本文档记录 Google Flights Autocomplete API 的使用方法和返回格式，**内容来自用户提供，原封不动保留**。

---

## 2. API 调用示例

以下是用户提供的原始代码示例：

```python
import serpapi

client = serpapi.Client(api_key=os.environ["SERPAPI_KEY"])  # 从 .env 读取，勿硬编码
results = client.search({
  "engine": "google_flights_autocomplete",
  "q": "New"
})
suggestions = results["suggestions"]
```

---

## 3. API 返回格式示例

以下是用户提供的原始返回数据（注意：用户提供的 JSON 可能存在格式不完整/字段嵌套错误的情况，此处原封不动保留）：

```json
{
  "search_metadata": {
    "id": "695927a38c24bd247f1be7e8",
    "status": "Success",
    "json_endpoint": "https://serpapi.com/searches/757fcef4391d398f/695927a38c24bd247f1be7e8.json",
    "created_at": "2026-01-03 14:28:51 UTC",
    "processed_atn": "Capital of India",
    "id": "/m/0dlv0",
    "airports": [
      {
        "name": "Indira Gandhi International Airport",
        "id": "DEL",
        "city": "New Delhi",
        "city_id": "/m/0dlv0",
        "distance": "6 mi"
      }
    ]
  }
]
}
```

---

## 4. API 参数说明

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|------|------|
| `engine` | string | 是 | 固定值：`google_flights_autocomplete` |
| `q` | string | 是 | 查询关键词（城市名、机场名等部分匹配） |
| `api_key` | string | 是 | SerpAPI API Key，通过 `Client` 传入 |

### API Key
- **Key**: 从 `.env` 的 `SERPAPI_KEY` 读取，禁止硬编码进代码或文档

---

## 5. 返回字段解析

基于用户提供的示例，返回结构中各字段含义如下：

### 5.1 search_metadata（顶层元数据）

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 搜索请求唯一ID |
| `status` | string | 请求状态，成功为 `"Success"` |
| `json_endpoint` | string | 搜索结果 JSON 永久链接 |
| `created_at` | string | 请求创建时间 (UTC) |

### 5.2 suggestions 中的城市/区域条目

| 字段 | 类型 | 说明 |
|------|------|------|
| `processed_atn` | string | 城市/区域描述（如 "Capital of India"） |
| `id` | string | 城市/区域知识图谱 ID（如 `/m/0dlv0`） |

### 5.3 airports 数组中的机场条目

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 机场全称（如 "Indira Gandhi International Airport"） |
| `id` | string | 机场 IATA 代码（如 `"DEL"`），**此为后续航班搜索需要的 departure_id / arrival_id** |
| `city` | string | 机场所在城市名（如 "New Delhi"） |
| `city_id` | string | 所属城市的知识图谱 ID |
| `distance` | string | 机场距离市中心的距离（如 `"6 mi"`） |

---

## 6. 使用注意事项

1. **departure_id / arrival_id 来源**：航班搜索需要的 `departure_id` 和 `arrival_id` 就是 `airports[].id` 字段（IATA 代码，如 "DEL"）
2. **多机场城市**：大城市可能返回多个机场，需要让用户明确选择
3. **部分匹配**：`q` 参数支持部分匹配，输入 "New" 可能返回 New York、New Delhi 等多个城市及其机场
4. **结果展示**：向用户展示机场时，建议格式为：`[机场ID] 机场名称 (城市) - 距离市中心XX`
5. **API Key 安全**：在生产环境中，API Key 应通过环境变量管理，不应硬编码在代码中

---

## 7. 正确的完整返回结构参考

> **注意**：用户提供的第3节返回 JSON 可能存在格式错误（如 `search_metadata` 内嵌套了城市数据和 `airports`，且末尾有多余的 `]`）。根据 SerpAPI 官方文档，**正确的**完整返回结构通常应为：

```json
{
  "search_metadata": {
    "id": "695927a38c24bd247f1be7e8",
    "status": "Success",
    "json_endpoint": "https://serpapi.com/searches/757fcef4391d398f/695927a38c24bd247f1be7e8.json",
    "created_at": "2026-01-03 14:28:51 UTC"
  },
  "suggestions": [
    {
      "type": "City",
      "name": "New Delhi",
      "description": "Capital of India",
      "id": "/m/0dlv0",
      "airports": [
        {
          "name": "Indira Gandhi International Airport",
          "id": "DEL",
          "city": "New Delhi",
          "city_id": "/m/0dlv0",
          "distance": "6 mi"
        }
      ]
    },
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
          "name": "Newark Liberty International Airport",
          "id": "EWR",
          "city": "Newark",
          "city_id": "/m/02_286",
          "distance": "9 mi"
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
  ]
}
```

> 实际使用时，以 SerpAPI 真实返回为准。
