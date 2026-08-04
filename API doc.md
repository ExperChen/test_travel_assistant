### 接口名称：AI 生成专属行程计划

**基本信息**

- **请求路径：** `/api/v1/agent/generate_itinerary`
- **请求方法：** `POST`
- **接口描述：** 接收用户的出行需求，通过 Agent 调度底层工具，返回结构化的航班、酒店及景点行程安排。
- **Content-Type：** `application/json`

<br />

### 请求参数

{
"input": "我要从吉隆坡到曼谷,帮我制定一个3天的旅游计划，包括景点，机票与酒店"
}

### 1. 响应参数 (Response Body)

这是后端 Agent 解析完成后，必须严格返回给前端的数据格式。

| **字段名**   | **类型**  | **说明**                  |
| --------- | ------- | ----------------------- |
| `code`    | Integer | 业务状态码 (200 为成功)         |
| `message` | String  | 提示信息                    |
| `data`    | Object  | 核心数据包，包含航班、酒店、景点，自然语言输出 |

**响应 JSON 示例（核心契约）：**

```json
    {
      "code": 200,
      "message": "success",
      "data": {
        "input": "我要从吉隆坡到曼谷,帮我制定一个3天的旅游计划，包括景点，机票与酒店",
        "output": "我为你制定了一个3天的旅游计划，包括景点、机票与酒店。以下是详细信息：",
        "flights": [
          {
            "name": "Malaysia Airlines",
            "code": "MH782",
            "airline_company": "MAS",
            "departure_airport": "KUL",
            "arrival_airport": "BKK",
            "departure_date": "2026-03-26T14:00:00",
            "arrival_date": "2026-03-26T15:10:00",
            "price": 450.00,
            "luggage_limitation": "20kg"
          }
        ],
        "hotels": [
          {
            "name": "Bangkok Marriott Hotel Sukhumvit",
            "location": "Sukhumvit Road, Bangkok",
            "arrive_date": "2026-03-26",
            "leave_date": "2026-03-28",
            "price": 1200.00,
            "rating": 4.8,
            "map_source": "https://maps.google.com/...",
            "hotel_source": "Booking.com" 
          }
        ],
        "views": [
          {
            "name": "The Grand Palace",
            "location": "Phra Nakhon, Bangkok",
            "information": "泰国历史名胜，前皇室居所。",
            "price": 500.00,
            "open_time": "08:30-15:30",
            "arrival_time": "2026-03-27T09:00:00",
            "departure_time": "2026-03-27T12:00:00",
            "visit_duration": "3 hours",
            "image":"https://lh3.googleusercontent.com/gps-cs-s/AHVAweq_EMFO_mwHLUthYQ_6BFeb4-EHYSQV95PlShD-8uw9er_SB-_Q5LMF0LlXCLgKPJLlGc6htUQdWNw2pQhEfHn_8G5BMHAdphhrujbir-ITgZRXYod1rjTD02aw-R3Nk0zuhqE=w540-h312-n-k-no"
          }
        ]
      }
    }

```

### 2.状态与错误码 (Error Codes)

前端需根据以下状态码进行相应的 UI 提示（如弹窗报错）。

- `200`: 请求成功，行程生成完毕。
- `400`: 请求参数错误（如时间格式错误、未提供目的地）。
- `500`: 服务器内部错误（如大模型调用超时、爬虫工具崩溃）。
- `502`: 第三方服务异常（如获取航班接口无响应）。

