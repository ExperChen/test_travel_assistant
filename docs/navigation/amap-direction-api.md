# 高德地图 路径规划 & 距离测量 API 工具文档

> 来源：<https://developer.amap.com/api/webservice/guide/api/direction>（最后更新：2026-02-02）

---

## 1. 产品概述

高德路径规划 API 是 HTTP GET 接口，提供 5 种出行方式的路线计算 + 1 种距离批量测量：

| 场景 | 接口路径 | 最大范围/限制 |
|------|----------|---------------|
| 步行 | `https://restapi.amap.com/v3/direction/walking` | ≤ 100 km |
| 公交综合换乘（市内/跨城含火车/飞机） | `https://restapi.amap.com/v3/direction/transit/integrated` | — |
| 驾车（小客车/轿车） | `https://restapi.amap.com/v3/direction/driving` | 支持 16 途经点、限行、避让区、轮渡 |
| 骑行（自行车/电动车） | `https://restapi.amap.com/v4/direction/bicycling` | ≤ 500 km（考虑天桥/单行/封路） |
| 批量距离测量 | `https://restapi.amap.com/v3/distance` | 一次最多 100 起点 × 1 终点；直线/驾车/步行三种算法 |

**前置要求**：申请「Web 服务 API」类型 Key，存入 `.env` 的 `AMAP_KEY`（与 POI 搜索是同一个 Key）。

---

## 2. 通用参数与通用响应

### 2.1 通用请求参数（所有接口通用）

| 参数 | 说明 | 必填 | 默认 |
|------|------|------|------|
| `key` | 高德 Web 服务 Key | ✅ 是 | 无 |
| `output` | `json` / `xml` | 否 | `json` |
| `callback` | JSONP 函数名，仅 output=json 时生效 | 否 | 无 |
| `sig` | 数字签名（控制台开启时必填） | 否 | 无 |

### 2.2 通用响应（距离测量除外，v3 系列通用）

```json
{
  "status": "1",       // "1"成功 "0"失败
  "info":   "OK",      // 成功=OK，失败=错误原因
  "infocode":"10000",  // 10000=正确
  "count":  "1",       // 方案数
  "route": { /* 路线对象 */ }
}
```

常见 `infocode`：`10000` 成功 / `10001` key 错 / `10002` QPS超限 / `10003` 白名单不符 / `10004` 余额不足 / `20000` 业务参数错误（如经纬度格式）。详见 <https://developer.amap.com/api/webservice/guide/tools/info>。

---

## 3. 场景一：步行路径规划 `/v3/direction/walking`

### 3.1 请求参数

| 参数 | 必填 | 规则 |
|------|------|------|
| `key` | ✅ | Web 服务 Key |
| `origin` | ✅ | 起点 `"经度,纬度"`，小数点 ≤ 6 位，例 `116.434307,39.90909` |
| `destination` | ✅ | 终点坐标，同上 |
| `origin_id` | 否 | 起点 POI ID（能提升精度） |
| `destination_id` | 否 | 终点 POI ID |
| `sig / output / callback` | 否 | 见 2.1 通用参数 |

服务示例 URL：
```
https://restapi.amap.com/v3/direction/walking?origin=116.434307,39.90909&destination=116.434446,39.90816&key=<AMAP_KEY>
```

### 3.2 返回字段

顶层 `route` 对象：
```jsonc
{
  "origin": "116.434307,39.90909",
  "destination": "116.434446,39.90816",
  "paths": [{
    "distance": "147",     // 总步行距离（米）
    "duration": "126",     // 总步行时间（秒）
    "steps": [{            // 每一段步行指引
      "instruction": "步行77米后右转",
      "road": "阜荣街",
      "distance": "77",
      "orientation": "东",
      "duration": "66",
      "polyline": "116.434...,39.909...;116.434...,39.909...",
      "action": "右转",               // 主要动作
      "assistant_action": "到达目的地",// 辅助动作
      "walk_type": "0"                // 0普通/1人行横道/3地下通道/4过街天桥/5地铁通道/6公园/7广场/8扶梯/9直梯/10索道/13行人通道/14游船/15观光车/20阶梯/21斜坡/22桥/23隧道/30轮渡
    }]
  }]
}
```

**步行动作枚举**（action / assistant_action）：左转、右转、左前/右前、左后/右后、直行、靠左/靠右、通过人行横道/过街天桥/地下通道/广场、到达目的地、进入右侧道路/左侧道路等。

---

## 4. 场景二：公交综合换乘 `/v3/direction/transit/integrated`

支持市内公交/地铁 + 跨城高铁/动车/飞机/长途汽车等组合换乘。

### 4.1 请求参数

| 参数 | 必填 | 规则 |
|------|------|------|
| `key` | ✅ | Web 服务 Key |
| `origin` | ✅ | 起点坐标 `"lon,lat"` |
| `destination` | ✅ | 终点坐标 `"lon,lat"` |
| `city` | ✅ | **起点城市**：城市名 / citycode（如 `"北京市"` / `"010"`） |
| `cityd` | 条件 | **跨城必填**：终点城市名 / citycode |
| `extensions` | 否 | `base`（默认）或 `all`（返回途经站点/备选方案/火车经停等） |
| `strategy` | 否 | 换乘策略（默认0）：0=最快捷，1=最经济，2=最少换乘，3=最少步行，5=不乘地铁 |
| `nightflag` | 否 | `0`（默认）不考虑夜班车 / `1` 计算夜班车 |
| `date` | 否 | 出发日期 `YYYY-M-D`，例 `2026-8-10`（不加参数=实时） |
| `time` | 否 | 出发时间 `HH:mm`，例 `08:30` |
| `sig/output/callback` | 否 | 通用参数 |

服务示例（北京望京 → 中关村 公交）：
```
https://restapi.amap.com/v3/direction/transit/integrated?origin=116.481499,39.990475&destination=116.315063,39.999538&city=010&extensions=all&strategy=2&key=<AMAP_KEY>
```

### 4.2 返回字段（route → transits[]）

```jsonc
{
  "status":"1","info":"OK","count":"3",
  "route":{
    "origin":"116.481499,39.990475",
    "destination":"116.315063,39.999538",
    "distance":"25000",           // 全程步行距离
    "taxi_cost":"82",             // 打车参考费（元）
    "transits":[{                 // 每个方案
      "cost": "6",                // 本方案总票价（元）
      "duration": "3900",         // 总耗时秒
      "nightflag": "0",           // 是否夜班车
      "walking_distance": "1850", // 总步行米
      "segments": [{              // 每一段（步行 + 一段公交/地铁/火车 组合）
        "walking": { /* 步行方案，同 walking API 的单段 steps 结构 */ },
        "bus": {
          "buslines": [{
            "departure_stop": { "name":"望京西站","id":"...","location":"..." },
            "arrival_stop":   { "name":"中关村站","id":"...","location":"..." },
            "name":"地铁15号线(俸伯--清华东路西口)",
            "id":"...",
            "type":"地铁线路",
            "distance":"20000",
            "duration":"2400",
            "polyline":"...;...",
            "start_time": "0530",  // 首班时刻 HHmm
            "end_time":   "2315",  // 末班时刻
            "via_num":  "7",       // 途经站数
            "via_stops":[{"name":"望京东","id":"...","location":"..."}, /*...*/] // extensions=all 时返回
          }]
        },
        "entrance": { "name":"C 东南口","location":"..." }, // 地铁入口（地铁路段才有）
        "exit":     { "name":"A 西北口","location":"..." }, // 地铁出口
        "railway": { /* extensions=all 且跨城乘火车时返回 */
          "id":"...", "time":"7200", "name":"京沪高铁", "trip":"G101",
          "type":"2012",  // 2010普客/2011G高铁/2012D动车/2013C城际/2014Z直达/2015T特快/2016K快车/2017LY临客/2018S郊线
          "departure_stop": { "id":"...","name":"北京南","adcode":"110106","time":"07:00","start":"1" },
          "arrival_stop":   { "id":"...","name":"上海虹桥","adcode":"310112","time":"12:23","end":"1"   },
          "spaces": [{ "code":"13","cost":"553" }] // 10硬座/11软座/12一等座/13二等座/14-16硬卧上中下/17-19软卧等/30飞机经济舱/31商务舱/40-43客轮
        }
      }]
    }]
  }
}
```

---

## 5. 场景三：驾车路径规划 `/v3/direction/driving`

### 5.1 请求参数（精选）

| 参数 | 必填 | 规则/说明 |
|------|------|-----------|
| `key` | ✅ | Web 服务 Key |
| `origin` | ✅ | **1~3 个坐标对**（可算车头朝向）：`"x1,y1\|x2,y2\|x3,y3"`，最后一个是真实起点；普通单起点直接 `"lon,lat"` 即可 |
| `destination` | ✅ | 终点坐标 `"lon,lat"` |
| `originid / destinationid / destinationtype` | 否 | 起点/终点 POI ID 与类别，能提升抓路精度 |
| `strategy` | 否 | **核心：选路线策略**，0~9 返回 1 条；10~20 返回多条（强烈推荐用 10~20）。完整枚举见 5.2 |
| `waypoints` | 否 | **途经点**，最多 16 个；`"lon1,lat1;lon2,lat2;..."` |
| `avoidpolygons` | 否 | **避让区域**，最多 32 个，每区最多 16 顶点；`"lon1,lat1;...;lonn,latn\|..."`；单区域 ≤ 81km² |
| `province` | 否 | 车牌省份（汉字），如 `"京"` → 用于限行判断 |
| `number` | 否 | 车牌字母+数字（大写，6/7 位均支持），例 `"NH1N11"` → 用于限行 |
| `cartype` | 否 | 车辆类型：`0`=普通油车(默认) / `1`=纯电 / `2`=插电混动 |
| `ferry` | 否 | `0`=允许轮渡(默认) / `1`=不允许 |
| `roadaggregation` | 否 | `false`(默认) / `true`：在 steps 之上返回 roads 做道路聚合 |
| `nosteps` | 否 | `0`(默认)返回详细 steps / `1`=只返回概要，不返回 steps |
| `extensions` | ✅ 必传但有默认 | `base`(默认) / `all`。**强烈推荐 `all`**，返回 taxi_cost、tmcs 路况、cities 途经、restriction 限行 |
| `sig/output/callback` | 否 | 通用参数 |

### 5.2 strategy 全枚举

| 值 | 含义 | 路径条数 |
|----|------|----------|
| **10** | ⭐ 默认推荐（高德App默认）：躲避拥堵+路程短+时间短 | 多条 |
| 11 | 时间最短 + 距离最短 + 躲避拥堵（各一条，共3条；已被 10 取代） | 多条 |
| 12 | 躲避拥堵优先（高德App"躲避拥堵"） | 多条 |
| 13 | 不走高速 | 多条 |
| 14 | 避免收费优先 | 多条 |
| 15 | 躲避拥堵 + 不走高速 | 多条 |
| 16 | 避免收费 + 不走高速 | 多条 |
| 17 | 躲避拥堵 + 避免收费 | 多条 |
| 18 | 躲避拥堵 + 避免收费 + 不走高速 | 多条 |
| 19 | 高速优先 | 多条 |
| 20 | 高速优先 + 躲避拥堵 | 多条 |
| 0 | 速度优先（可能不是最短） | 1条 |
| 1 | 费用优先（不走收费） | 1条 |
| 2 | 常规最快（综合距离+时间） | 1条 |
| 3 | 速度优先不走快速路（建议用13代替） | 1条 |
| 4 | 躲避拥堵（可能绕路） | 1条 |
| 5 | 多策略（速度/费用/距离各算一次，动态返回1-3条） | 1-3条 |
| 6 | 速度优先，不走高速但可能走其他收费段 | 1条 |
| 7 | 费用优先，不走高速+不收任何费 | 1条 |
| 8 | 躲避拥堵+收费（可能走高速） | 1条 |
| 9 | 躲避拥堵+收费+不走高速 | 1条 |

> 默认值：`strategy=0`。在 Travel Assistant 中做自驾游推荐 → 用 `strategy=10`；乘客想省钱 → `strategy=14` 或 `7`。

### 5.3 返回字段

```jsonc
{
  "status":"1","info":"OK","count":"2",
  "route":{
    "origin":"116.481499,39.990475",
    "destination":"116.434446,39.90816",
    "taxi_cost":"78",            // extensions=all 时才有，出租车估价元
    "paths":[{                   // 每条路线
      "distance":"21000",        // 全程米
      "duration":"2400",         // 全程秒
      "strategy":"10",           // 本方案实际使用的策略码
      "tolls":"5",               // 过路费总计（元）
      "toll_distance":"8000",    // 收费路段米
      "restriction":"0",         // 0=无限行路段 1=有限行无法规避
      "traffic_lights":"17",     // 红绿灯个数
      "steps":[{                 // 每个路段
        "instruction":"沿广泽桥向西行驶500米后驶入京密路",
        "orientation":"西",
        "road":"京密路/G101",
        "distance":"2500",
        "tolls":"0",
        "toll_distance":"0",
        "toll_road":"",          // 本段主线收费道路名
        "polyline":"...;...",
        "action":"靠左",
        "assistant_action":"进入高速",
        "tmcs": [{               // extensions=all 时本段路况分段
          "distance":"300",
          "status":"畅通",       // 未知/畅通/缓行/拥堵/严重拥堵
          "polyline":"...;..."
        }]
      }],
      "cities": [{               // extensions=all 时途经行政区划
        "name":"北京市","citycode":"010",
        "districts":[{"name":"朝阳区","adcode":"110105"},{"name":"东城区","adcode":"110101"}]
      }]
    }]
  }
}
```

### 5.4 驾车动作（extensions=all 时生效）

- **主要动作**：左转/右转/左前/右前/左后/右后/调头/直行/靠左/靠右/进入环岛/离开环岛/减速行驶
- **辅助动作**（50+种）：进入主路/辅路/高速/匝道/隧道/中间岔道/右岔/左岔/左右转专用道；驶离轮渡；沿主路/辅路行驶；到达出口/服务区/收费站/途经地；环岛 1~5 出口；复杂路口左右 1~5 出口；进入调头专用路。

---

## 6. 场景四：骑行路径规划 `/v4/direction/bicycling`

### 6.1 请求参数

| 参数 | 必填 | 规则 |
|------|------|------|
| `key` | ✅ | Web 服务 Key |
| `origin` | ✅ | 起点 `"lon,lat"`（≤6位小数） |
| `destination` | ✅ | 终点 `"lon,lat"` |

> v4 版接口 URL 不同（`/v4/direction/bicycling`），响应外层结构使用 `{data, errcode, errmsg}`，不再有 `info/count`。

### 6.2 返回字段

```jsonc
{
  "errcode": 0,
  "errmsg":  "OK",
  "errdetail": null,
  "data": {
    "origin": "116.481499,39.990475",
    "destination": "116.434446,39.90816",
    "paths": [{
      "distance": 14500,   // 骑行米
      "duration": 3600,    // 骑行秒
      "steps": [{
        "instruction": "骑行54米右转进入阜荣街",
        "road": "阜荣街",         // 注意：可能为 null
        "distance": 54,
        "orientation": "东",
        "duration": 8,
        "polyline": "...;...",
        "action": "右转",
        "assistant_action": "到达目的地"
      }]
    }]
  }
}
```

---

## 7. 场景五：批量距离测量 `/v3/distance`

> 常用于：快速回答"机场到酒店 A/B/C 各要多少时间？"、"给 10 家景点按距当前酒店驾车距离排序"。

### 7.1 请求参数

| 参数 | 必填 | 规则 |
|------|------|------|
| `key` | ✅ | Web 服务 Key |
| `origins` | ✅ | 一批起点坐标，`"lon1,lat1\|lon2,lat2\|...\|lon100,lat100"`，最多 **100 个** |
| `destination` | ✅ | 1 个终点坐标 `"lon,lat"` |
| `type` | 否 | **计算方式**：`0`=直线（不考虑道路）/ `1`=驾车导航（默认，同 driving strategy=4 躲避拥堵，结果随路况变动）/ `3`=步行（仅 5km 内准确） |
| `sig/output/callback` | 否 | 通用参数 |

URL 示例：
```
https://restapi.amap.com/v3/distance?origins=116.481,39.990|116.315,39.999|116.434,39.908&destination=116.403,39.915&type=1&key=<AMAP_KEY>
```

### 7.2 返回字段

```jsonc
{
  "status":"1","info":"OK","infocode":"10000",
  "results":[
    { "origin_id":"1","dest_id":"1","distance":"12050","duration":"2700" },  // 第一个起点到终点
    { "origin_id":"2","dest_id":"1","distance":"14320","duration":"3100" },  // 第二个起点到终点
    { "origin_id":"3","dest_id":"1","distance": "5200","duration":"1460" }   // 第三个
  ]
}
```

**单条失败时**：`results[i]` 会多出 `info`（错误原因，通常"未知错误"）+ `code`：
- code=1：两点间无可行车道路
- code=2：起点/终点远离任何道路（在海洋/矿山）
- code=3：起点/终点不在中国境内

---

## 8. Python 完整工具示例（requests + dotenv）

```python
"""
高德路径规划工具：步行/公交/驾车/骑行/距离测量
安装依赖: pip install requests python-dotenv
"""
import os
from urllib.parse import urlencode
import requests
from dotenv import load_dotenv

load_dotenv()
AMAP_KEY = os.getenv("AMAP_KEY", "")


def _check(data: dict) -> dict:
    """统一做 v3 接口返回检查；v4 接口请另外判断"""
    if data.get("status") not in ("1", 1):
        raise RuntimeError(f"高德API错误 status={data.get('status')} info={data.get('info')} infocode={data.get('infocode')}")
    return data


# --------- 步行 ---------
def walking(origin: str, destination: str, origin_id=None, destination_id=None) -> dict:
    url = f"https://restapi.amap.com/v3/direction/walking?{urlencode({
        'key': AMAP_KEY, 'origin': origin, 'destination': destination,
        **({'origin_id': origin_id} if origin_id else {}),
        **({'destination_id': destination_id} if destination_id else {}),
        'output': 'json',
    })}"
    return _check(requests.get(url, timeout=15).json())


# --------- 公交/跨城换乘 ---------
def transit_integrated(origin: str, destination: str, city: str, cityd: str | None = None,
                       extensions: str = "all", strategy: int = 0, nightflag: int = 0,
                       date: str | None = None, time: str | None = None) -> dict:
    params = {"key": AMAP_KEY, "origin": origin, "destination": destination,
              "city": city, "extensions": extensions, "strategy": strategy, "nightflag": nightflag}
    if cityd: params["cityd"] = cityd
    if date:  params["date"]  = date
    if time:  params["time"]  = time
    url = f"https://restapi.amap.com/v3/direction/transit/integrated?{urlencode(params)}"
    return _check(requests.get(url, timeout=20).json())


# --------- 驾车 ---------
def driving(origin: str, destination: str, strategy: int = 10, extensions: str = "all",
            waypoints: list[str] | None = None,
            avoidpolygons: list[list[str]] | None = None,
            province: str | None = None, number: str | None = None, cartype: int = 0,
            ferry: int = 0, nosteps: int = 0) -> dict:
    """
    :param waypoints:      途经点经纬度列表，最多16个
    :param avoidpolygons:  避让多边形列表，每个多边形是 ["lon1,lat1","lon2,lat2"...]
    :param province:       车牌省（汉字），例 "京"
    :param number:         车牌后6/7位（大写），例 "NH1N11"
    """
    params = {
        "key": AMAP_KEY, "origin": origin, "destination": destination,
        "strategy": strategy, "extensions": extensions,
        "cartype": cartype, "ferry": ferry, "nosteps": nosteps,
    }
    if waypoints:  params["waypoints"]     = ";".join(waypoints[:16])
    if avoidpolygons:
        polys = []
        for poly in avoidpolygons[:32]:
            polys.append(";".join(poly[:16]))
        params["avoidpolygons"] = "|".join(polys)
    if province: params["province"] = province
    if number:   params["number"]   = number
    url = f"https://restapi.amap.com/v3/direction/driving?{urlencode(params)}"
    return _check(requests.get(url, timeout=20).json())


# --------- 骑行 ---------
def bicycling(origin: str, destination: str) -> dict:
    url = f"https://restapi.amap.com/v4/direction/bicycling?{urlencode({
        'key': AMAP_KEY, 'origin': origin, 'destination': destination,
    })}"
    data = requests.get(url, timeout=15).json()
    if data.get("errcode") != 0:
        raise RuntimeError(f"骑行API错误 errcode={data.get('errcode')} errmsg={data.get('errmsg')} detail={data.get('errdetail')}")
    return data


# --------- 批量距离测量 ---------
def distance_batch(origins: list[str], destination: str, type_: int = 1) -> list[dict]:
    """
    :param origins: 起点坐标列表，最多100
    :param destination: 终点坐标（单一）
    :param type_: 0=直线 1=驾车(默认) 3=步行
    :return: results 列表，每项 {origin_id, dest_id, distance 米, duration 秒}
    """
    url = f"https://restapi.amap.com/v3/distance?{urlencode({
        'key': AMAP_KEY,
        'origins': '|'.join(origins[:100]),
        'destination': destination,
        'type': type_, 'output': 'json'
    })}"
    data = _check(requests.get(url, timeout=15).json())
    return data.get("results", [])


if __name__ == "__main__":
    if not AMAP_KEY:
        raise SystemExit("请先在 .env 中填入 AMAP_KEY")

    # 例1 驾车：望京→天安门 多策略推荐（默认10），extensions=all 含打车费/路况/途经行政区
    r = driving("116.481499,39.990475", "116.403963,39.915119",
                strategy=10, extensions="all", province="京", number="NH1N11")
    for i, p in enumerate(r["route"]["paths"], 1):
        km = int(p["distance"]) / 1000
        min_ = int(p["duration"]) // 60
        print(f"路线{i}: {km:.1f}km / {min_}分钟 / 过路费{p.get('tolls')}元 / 策略{p.get('strategy')} "
              f"/ 限行={p.get('restriction')} / 红绿灯{p.get('traffic_lights')}个")

    # 例2 批量距离：3个景点→酒店 驾车耗时
    res = distance_batch(origins=[
        "116.397428,39.90923",   # 故宫
        "116.403963,39.915119",  # 天安门
        "116.391019,39.908097",  # 天坛
    ], destination="116.434446,39.90816", type_=1)
    for r in res:
        print(f"起点{r['origin_id']}: {int(r['distance'])/1000:.1f}km，驾车约 {int(r['duration'])//60} 分钟")
```

---

## 9. Travel Assistant 场景的 Tool 设计建议

在景点/行程规划 Agent 里，建议把高德方向能力封装成 3 个高价值 Tool（其余 2 个在 Tool 描述里作为补充）：

### 9.1 Tool: `amap_direction_driving`

- **入参**：`origin_lon`, `origin_lat`, `destination_lon`, `destination_lat` (必)；`strategy`（默认10，给 0~20 枚举说明）；`waypoints_lonlat_list`（可选 16 个 [lon,lat]）；`plate_province`, `plate_number`, `car_type`（0油/1纯电/2插混，默认0）
- **返回给 LLM**：每条路线只取 `distance/米 → 转 km`, `duration/秒 → 转 分钟`, `tolls 元`, `restriction 限行`, `traffic_lights`, 以及 top-3 steps 的 `instruction + 路名 + 距离`（不要把整条 polyline 扔给 LLM，超 token）
- **典型场景**："从北京自驾到秦皇岛走哪条高速最快？"、"按我的尾号限行帮我规划明天去长城的路线"

### 9.2 Tool: `amap_direction_transit`

- **入参**：`origin_lon,origin_lat,destination_lon,destination_lat,city,cityd?,strategy?,extensions=all,date?,time?`
- **典型场景**：用户说"早上 8 点从上海虹桥站到迪士尼最快的地铁路线" → `transit_integrated(... strategy=0 extensions=all date=... time=08:00)`

### 9.3 Tool: `amap_distance_batch`

- **入参**：`origins: list[lonlat]`, `destination: lonlat`, `type`（0/1/3）
- **返回**：列表排序，按 `duration` 或 `distance` 升序；在对用户推荐时可直接"最近的3个景点：XX 驾车 18 分钟、YY 22 分钟、ZZ 35 分钟"
- **典型场景**："我现在在西湖，帮我挑 5 个周边 30 分钟车程内值得去的景点，按到达时间从快到慢排"

步行和骑行 Tool 可以从简，按需做成 `amap_direction_walking` 与 `amap_direction_bicycling`，参数同 Python 函数。

---

## 10. 注意事项 & 坑

1. **坐标系**：所有 API 的经纬度统一是 **GCJ-02（火星坐标）**，与高德 POI/输入提示/地图 JS SDK 都能直接互通；但不要和谷歌地图/Apple Maps 的 WGS-84、百度的 BD-09 混着传（结果会"飘"）。Travel Assistant 里所有位置从 **POI.location** 取出来的就不用再转。
2. **驾车 extensions 强烈选 all**：否则拿不到 taxi_cost、限行 restriction、路况 tmcs、途径行政 cities，LLM 无法回答"大概要多少打车费"、"会不会限行"这类关键问题。
3. **驾车 strategy 强烈选 10**（默认是 0，只给 1 条，经常不优）。给用户展示建议取 3 条（count 可能给 2~5 条）。
4. **驾车 origin 可以传多个算车头朝向**：用户从酒店出发时，可传 `前一个定位|酒店门口真实定位` 两个点，规划从哪侧进门更合理。
5. **步行 walk_type 别漏**：当存在 "1=人行横道 / 22=桥 / 23=隧道" 时，给用户的指引文案里要加"请走人行横道过街"、"穿过隧道"。
6. **骑行 road 字段可能 null**：老数据返回空字符串，新规范会返回 null，渲染要做容错。
7. **距离测量 type=1 的驾车距离是 strategy=4**：和真实驾车的 strategy=10 有 5~15% 的偏差（因为它走的是躲避拥堵老策略）；如果要精准，直接用 `driving(strategy=10)` 再取每条的 `distance/duration`。
8. **距离测量 type=3（步行）只适合 5km 内**；更大范围要走真实 walking 接口。
9. **公交/火车 extensions=all 才返回 spaces（席位/票价）**：用户要查票价/选座次时务必传 `extensions=all`。
10. **火车类型码在第 4 节 bus.railway.type**：2011=G 高铁、2012=D 动车、2013=C 城际… 席位码在 spaces.code（10硬座/12 一等座/13 二等座/30 经济舱…）。做"推荐二等座票价"时要按 code=13 过滤。
11. **请求频率**：免费配额 5k/日，QPS 50；路径规划比 POI 搜索更吃配额，批量推荐景点时优先用 distance_batch，不要对每个景点都调用一次 driving。
12. **错误码对照表**：<https://developer.amap.com/api/webservice/guide/tools/info>；流量限制表：<https://developer.amap.com/api/webservice/guide/tools/flowlevel>。
