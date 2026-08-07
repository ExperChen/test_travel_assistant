# 高德地图 POI 搜索 2.0 API 工具文档（景点搜索）

> 来源：<https://developer.amap.com/api/webservice/guide/api-advanced/newpoisearch>（最后更新：2026-07-15）

---

## 1. 产品概述

高德"搜索 POI 2.0"是 Web API 服务，提供 4 种地点搜索场景：

| 场景 | 用途 | 接口 URL 路径 |
|------|------|---------------|
| 关键字搜索 | 用文本（景点名/地址/POI名）搜索地点 | `/v5/place/text` |
| 周边搜索 | 以某坐标为圆心、半径内搜索（如"故宫附近 3km 内的餐厅/景点"） | `/v5/place/around` |
| 多边形搜索 | 自定义多边形区域内搜索 | `/v5/place/polygon` |
| ID 搜索 | 用已知 POI ID 查询该 POI 详情（建议结合输入提示接口用） | `/v5/place/detail`（官方惯例，实际以文档"ID搜索"小节说明为准） |

**通用约束**：
- 同参数翻页最多返回 **200 条**（page_size 最大 25，page_num 最大 8 = 25×8=200）
- 所有请求/响应编码 **UTF-8**
- 输出目前只支持 **JSON**（output=json 固定）
- 查询编码一律 UTF-8，URL 需要做 `application/x-www-form-urlencoded` 编码

---

## 2. 使用前置：申请 Key（Web 服务 API 类型）

1. 注册为高德开发者：<https://developer.amap.com/>
2. 进入控制台 → 应用管理 → 创建应用 → 添加 Key：
   - **服务平台**：必须选 **Web 服务 API**（不是 Android/iOS/JS API）
3. 拿到 Key 后，写入本项目的 `.env` 变量 `AMAP_KEY`：

```
AMAP_KEY=你的高德Web服务API_Key
```

---

## 3. 通用参数与通用响应字段

### 3.1 通用可选参数

所有接口都支持以下参数：

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|------|------|------|
| `key` | string | ✅ 是 | - | 高德 Web 服务 API Key |
| `output` | string | 否 | `json` | 输出格式，目前只支持 `json` |
| `callback` | string | 否 | - | JSONP 回调函数名 |
| `sig` | string | 否 | - | 数字签名（需在控制台开启，参考 <https://developer.amap.com/faq/quota-key/key/41181/>） |
| `page_size` | int | 否 | `10` | 每页条数，范围 1~25 |
| `page_num` | int | 否 | `1` | 第几页，默认第 1 页 |
| `show_fields` | string | 否 | 空 | 扩展字段，逗号分隔：`children,business,indoor,navi,photos`（见"返回字段"章节） |
| `langCode` | string | 否 | `zh` | 返回语言：`zh`=中文，`en`=英文（英文为高级功能，需商务工单开通） |

### 3.2 通用响应头字段

```json
{
  "status":   "1",          // "1"=成功 "0"=失败
  "info":     "OK",         // 成功=OK / 错误原因文本
  "infocode": "10000",      // 10000=正确，其他见错误码表
  "count":    "10",         // 本次实际返回 POI 数
  "pois":     [ /*...*/ ]   // POI 数组
}
```

常见 `infocode`：`10000` 成功 / `10001` key 不正确 / `10002` 请求太频繁 / `10003` 域名或 IP 白名单不匹配 / `10004` 余额不足 / `10014` 周边搜索的经纬度非法（详见 <https://developer.amap.com/api/webservice/guide/tools/info>）。

---

## 4. 场景一：关键字搜索（景点搜索核心）

### 4.1 接口信息

| 项 | 值 |
|----|----|
| URL | `https://restapi.amap.com/v5/place/text?parameters` |
| 方法 | HTTP GET |

### 4.2 请求参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | string | ✅ 是 | 高德 Key |
| `keywords` | string | 二选一必填 | **只支持单个关键字**，总长度 ≤ 80 字符。可以是 POI 名、地址等，如"西湖"、"故宫博物院"、"外滩"、"成都市宽窄巷子" |
| `types` | string | 二选一必填 | POI 分类码，多个用 `\|` 分隔（景点常用：`110000`=风景名胜，见第 7 节景点分类码）。**keywords 与 types 至少填一个** |
| `region` | string | 否 | 指定搜索区划：可填 citycode / adcode / 城市中文名（仅城市级），如 "北京市"、"310000"、"310101"。此参数只影响召回**权重**，不是严格过滤 |
| `city_limit` | bool | 否 | `true` 时，严格只返回 `region` 指定范围内的 POI；默认 `false` |
| + 通用可选参数 | — | — | page_size / page_num / show_fields / sig / langCode / output 等（见 3.1） |

### 4.3 服务示例 URL（把 `<你的key>` 替换成实际 AMAP_KEY）

```
https://restapi.amap.com/v5/place/text?keywords=西湖&types=110000|110101|110200&region=杭州市&city_limit=true&show_fields=business,photos&page_size=20&key=<你的key>
```

### 4.4 Python 调用示例（关键字搜索景点）

```python
"""
高德 POI 搜索 2.0 - 关键字搜索景点 示例
依赖:  pip install requests python-dotenv
"""
import os
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()                               # 读取 .env
AMAP_KEY = os.getenv("AMAP_KEY")           # 从 .env 读 AMAP_KEY

BASE_URL = "https://restapi.amap.com/v5/place/text"


def search_attractions_by_keyword(
    keyword: str,
    region: str | None = None,
    city_limit: bool = True,
    types: str = "110000|110101|110200",     # 默认只搜风景名胜+公园+文物古迹
    page_size: int = 20,
    page_num: int = 1,
    show_fields: str = "business,photos",
) -> dict:
    params = {
        "key": AMAP_KEY,
        "keywords": keyword,
        "types": types,
        "page_size": page_size,
        "page_num": page_num,
        "show_fields": show_fields,
        "output": "json",
    }
    if region:
        params["region"] = region
        if city_limit:
            params["city_limit"] = "true"
    url = f"{BASE_URL}?{urlencode(params)}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "1":
        raise RuntimeError(f"高德API错误: info={data.get('info')} infocode={data.get('infocode')}")
    return data


if __name__ == "__main__":
    result = search_attractions_by_keyword("西湖", region="杭州市")
    print(f"共返回 {result['count']} 个POI")
    for poi in result.get("pois", []):
        rating = (poi.get("business") or {}).get("rating") or "-"
        cost = (poi.get("business") or {}).get("cost") or "-"
        photos = (poi.get("photos") or [])
        photo_url = photos[0]["url"] if photos else ""
        print(f"[{poi['id']}] {poi['name']}  评分:{rating}  消费:{cost}")
        print(f"    地址: {poi.get('pname')}{poi.get('cityname')}{poi.get('adname')} {poi.get('address','')}")
        print(f"    坐标: {poi.get('location')}   类型: {poi.get('type')}")
        if photo_url:
            print(f"    图: {photo_url}")
        print()
```

---

## 5. 场景二：周边搜索（按经纬度 + 半径）

### 5.1 接口信息

| 项 | 值 |
|----|----|
| URL | `https://restapi.amap.com/v5/place/around?parameters` |
| 方法 | HTTP GET |

### 5.2 请求参数（相对关键字搜索的差异部分）

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `key` | string | ✅ 是 | 高德 Key |
| `location` | string | ✅ 是 | 圆心坐标：**经度,纬度**（逗号分隔，经度在前，小数点 ≤ 6 位）。例：`116.473168,39.993015`（望京 SOHO） |
| `radius` | int | 否 | 搜索半径（米），范围 0~50000，超过 50000 按默认；**默认 5000（5 公里）** |
| `keywords` | string | 否 | 同关键字搜索，但仅作为周边内的过滤词 |
| `types` | string | 否 | POI 分类码（与关键字搜索相同）；若 keywords+types 都为空，默认返回 `050000\|070000\|120000`（餐饮/生活服务/商务住宅） |
| `sortrule` | string | 否 | `distance`（按距离排序，默认）/ `weight`（综合排序）。**注意：只传 keywords 时 distance 排序不生效** |
| `region` + `city_limit` | string/bool | 否 | 同关键字搜索 |
| + 通用可选参数 | — | — | page_size/page_num/show_fields/sig/langCode/output 等 |

### 5.3 服务示例 URL

```
https://restapi.amap.com/v5/place/around?location=120.156209,30.274648&radius=2000&types=110000|110101&sortrule=distance&show_fields=business,photos&page_size=20&key=<你的key>
```
（注：120.156209,30.274648 ≈ 杭州西湖断桥附近，半径 2km 内找风景名胜+公园）

### 5.4 Python 调用示例（周边景点搜索）

```python
import os
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()
AMAP_KEY = os.getenv("AMAP_KEY")
BASE_URL = "https://restapi.amap.com/v5/place/around"


def search_attractions_around(
    longitude: float,
    latitude: float,
    radius_meters: int = 5000,
    keyword: str | None = None,
    types: str = "110000|110101|110200",
    sort_by_distance: bool = True,
    page_size: int = 20,
    page_num: int = 1,
    show_fields: str = "business,photos",
) -> dict:
    params = {
        "key": AMAP_KEY,
        "location": f"{longitude:.6f},{latitude:.6f}",
        "radius": max(0, min(50000, radius_meters)),
        "types": types,
        "sortrule": "distance" if sort_by_distance else "weight",
        "page_size": page_size,
        "page_num": page_num,
        "show_fields": show_fields,
        "output": "json",
    }
    if keyword:
        params["keywords"] = keyword
    url = f"{BASE_URL}?{urlencode(params)}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") != "1":
        raise RuntimeError(f"高德API错误: info={data.get('info')} infocode={data.get('infocode')}")
    return data


if __name__ == "__main__":
    # 以故宫（约 116.397428,39.90923）为中心 3km 范围搜景点
    r = search_attractions_around(116.397428, 39.90923, radius_meters=3000)
    print(f"返回 {r['count']} 个周边景点POI")
    for poi in r.get("pois", []):
        rating = (poi.get("business") or {}).get("rating") or "-"
        print(f"  · {poi['name']}   距离≈{int(poi.get('distance','0'))}m  评分:{rating}")
```

---

## 6. 场景三：多边形区域搜索 & 场景四：ID 搜索

### 6.1 多边形区域搜索

| 项 | 值 |
|----|----|
| URL | `https://restapi.amap.com/v5/place/polygon?parameters` |
| 方法 | HTTP GET |
| 必填 | `key` + `polygon`（多边形顶点，格式：`经度1,纬度1;经度2,纬度2;...;经度n,纬度n`，最后自动闭合；顶点 3~50 个） |
| 其他 | keywords / types / show_fields / page_size / page_num / region / city_limit / langCode / sig / output 与关键字搜索相同 |

示例 URL（一个矩形框范围内搜景点）：
```
https://restapi.amap.com/v5/place/polygon?polygon=116.35,39.88;116.45,39.88;116.45,39.95;116.35,39.95&types=110000|110101&key=<你的key>
```

### 6.2 ID 搜索（POI 详情）

根据单个/多个 POI ID 查询详情。可配合关键字搜索返回的 `poi.id` 做二次详情查询。

| 项 | 值 |
|----|----|
| URL | `https://restapi.amap.com/v5/place/detail?parameters` |
| 方法 | HTTP GET |
| 必填 | `key` + `id`（单个 POI ID，或多个 ID 用逗号分隔，最多 20 个） |
| 其他 | show_fields / langCode / output / sig （**不支持分页**） |

Python 片段：
```python
def get_poi_detail(poi_ids: list[str], show_fields="business,photos,navi,children") -> dict:
    url = "https://restapi.amap.com/v5/place/detail?" + urlencode({
        "key": AMAP_KEY,
        "id": ",".join(poi_ids[:20]),
        "show_fields": show_fields,
        "output": "json",
    })
    data = requests.get(url, timeout=15).json()
    if data.get("status") != "1":
        raise RuntimeError(f"{data.get('info')} infocode={data.get('infocode')}")
    return data
```

---

## 7. 常用 POI 分类码（景点场景）

完整分类码表需在高德文档下载：<https://developer.amap.com/api/webservice/download>（《POI 分类编码》）。**景点相关常用码**（所有以 `11` 开头的大类）：

| 分类码 | 含义 |
|--------|------|
| `110000` | 风景名胜（风景名胜区） |
| `110100` | 公园 |
| `110101` | 城市公园 |
| `110102` | 主题公园 |
| `110103` | 植物园 |
| `110104` | 动物园 |
| `110105` | 园林- |
| `110200` | 文物古迹 |
| `110201` | 世界遗产 |
| `110202` | 全国重点文物保护单位 |
| `110300` | 博物馆（含科技馆/展览馆） |
| `110301` | 博物馆 |
| `110302` | 展览馆 |
| `110303` | 美术馆 |
| `110400` | 纪念馆/牌坊 |
| `110500` | 钟楼/鼓楼/教堂/寺庙等宗教场所 |
| `110600` | 剧院/音乐厅 |
| `190101` | 度假疗养场所（度假村/疗养院） |

> 小技巧：只传 `types=110000` 相当于返回整个"风景名胜"大类。用 `\|` 组合多个码，扩大召回。

---

## 8. 返回字段说明

### 8.1 基础字段（**不传 show_fields 就会返回**）

| 字段 | 类型 | 说明 |
|------|------|------|
| `poi[].name` | string | POI 名称（如"西湖风景名胜区"） |
| `poi[].id` | string | POI 唯一 ID（后续详情/预订/导航 都是用它） |
| `poi[].parent` | string | 父 POI ID，可能空 |
| `poi[].location` | string | "经度,纬度"（GCJ-02 坐标系！**非 WGS84**，展示在高德地图直接用；配合其他地图需做坐标转换） |
| `poi[].distance` | string | 米。仅在**周边搜索**返回；关键字/多边形返回空串 |
| `poi[].type` | string | POI 类型中文，如"风景名胜;风景名胜;风景名胜" |
| `poi[].typecode` | string | 对应分类码，如"110000" |
| `poi[].pname` | string | 省名，如"浙江省" |
| `poi[].cityname` | string | 市名，如"杭州市" |
| `poi[].adname` | string | 区县名，如"西湖区" |
| `poi[].address` | string | 详细地址（不含省市区部分），如"北山街道" |
| `poi[].pcode` | string | 省编码，如"330000" |
| `poi[].citycode` | string | 市编码，如"0571" |
| `poi[].adcode` | string | 区/县编码，如"330106" |

### 8.2 扩展字段（需在 `show_fields=` 中显式声明，多个用逗号）

#### show_fields=business（商业信息，景点场景**最常用**）
| 字段 | 说明 |
|------|------|
| `poi[].business.business_area` | 所属商圈（如"西湖景区"） |
| `poi[].business.opentime_today` | 今日营业时间，如 `07:00-18:30` |
| `poi[].business.opentime_week` | 整周营业描述（含延时/节假日例外），需做富文本展示 |
| `poi[].business.tel` | 电话 |
| `poi[].business.rating` | 评分（仅**餐饮 / 酒店 / 景点 / 影院**返回），如 "4.8" |
| `poi[].business.cost` | 人均消费或门票参考价（景点适用），如 "70"（单位由实际场景推断） |
| `poi[].business.tag` | 特色标签（目前主要美食返回） |
| `poi[].business.alias` | POI 别名 |
| `poi[].business.keytag` / `rectag` | 二次确认 POI 信息类型的辅助标签 |
| `poi[].business.parking_type` | 仅停车场 POI：地下/地面/路边 |

#### show_fields=photos（图片）
| 字段 | 说明 |
|------|------|
| `poi[].photos[].title` | 图片说明 |
| `poi[].photos[].url` | 图片下载直链 |

#### show_fields=navi（导航相关）
| 字段 | 说明 |
|------|------|
| `poi[].navi.navi_poiid` | 导航引导点 POI ID（大门/入口/停车场入口） |
| `poi[].navi.entr_location` | 入口经纬度 |
| `poi[].navi.exit_location` | 出口经纬度 |
| `poi[].navi.gridcode` | 地理格 ID |

#### show_fields=children（子 POI）
`poi[].children[]`：子 POI 列表。例：一个大型景区下的小景点。字段：`id, name, location, address, subtype, typecode, sname`。

#### show_fields=indoor（室内）
`poi[].indoor.indoor_map / cpid / floor / truefloor`：是否有室内地图、楼层等。

---

## 9. 返回 JSON 示例（关键字搜索景点）

请求：`GET /v5/place/text?keywords=西湖&region=杭州市&types=110000|110101&show_fields=business,photos&page_size=3&key=...`

```json
{
  "status": "1",
  "info": "OK",
  "infocode": "10000",
  "count": "3",
  "pois": [
    {
      "name": "西湖风景名胜区",
      "id": "B0FFFABGHR",
      "parent": "",
      "location": "120.156209,30.274648",
      "distance": "",
      "type": "风景名胜;风景名胜;风景名胜",
      "typecode": "110000",
      "pname": "浙江省",
      "cityname": "杭州市",
      "adname": "西湖区",
      "address": "龙井路1号",
      "pcode": "330000",
      "citycode": "0571",
      "adcode": "330106",
      "business": {
        "business_area": "西湖景区",
        "opentime_today": "全天开放",
        "opentime_week": "全年 全天开放",
        "tel": "0571-87179613",
        "rating": "4.9",
        "cost": "免费"
      },
      "photos": [
        { "title": "西湖", "url": "https://store.is.autonavi.com/.../xxxx.jpg" }
      ]
    },
    {
      "name": "太子湾公园",
      "id": "B0FFFAB8G6",
      "location": "120.144412,30.229475",
      "type": "风景名胜;公园广场;公园",
      "typecode": "110101",
      "pname": "浙江省", "cityname": "杭州市", "adname": "西湖区",
      "address": "南山路5-1号",
      "business": { "rating": "4.7", "cost": "免费", "opentime_today": "07:00-17:00" },
      "photos": []
    },
    {
      "name": "雷峰塔景区",
      "id": "B0FFF8K3VZ",
      "location": "120.170687,30.231580",
      "type": "风景名胜;风景名胜;风景名胜",
      "typecode": "110000",
      "pname": "浙江省", "cityname": "杭州市", "adname": "西湖区",
      "address": "南山路15号",
      "business": { "rating": "4.6", "cost": "40", "opentime_today": "08:00-20:00", "tel": "0571-87982111" },
      "photos": [ { "title": "雷峰塔", "url": "https://..." } ]
    }
  ]
}
```

---

## 10. 景点场景参数推荐（即开即用）

### 10.1 关键字搜景点（强约束在某城市）
```
keywords=故宫
region=北京市
city_limit=true
types=110000|110101|110200|110300
show_fields=business,photos,navi
page_size=20
```

### 10.2 我在某经纬度，搜"附近的景点+博物馆"，按距离
```
location=120.156209,30.274648
radius=3000
types=110000|110100|110200|110300
sortrule=distance
show_fields=business,photos
page_size=25
```

### 10.3 搜一个已选中 POI 的全量详情
```
id=B0FFFABGHR
show_fields=business,photos,navi,children
```

---

## 11. 注意事项与常见坑

1. **坐标系**：POI.location 是 **GCJ-02（火星坐标）**。如果要在 Google Maps / WGS84 上显示，必须做坐标转换。
2. **Key 类型**：申请的 Key **必须是 "Web 服务 API"** 类型，JS/Android/iOS 类型 Key 调用会报 10001/10003。
3. **分页上限 200 条**：同一组参数，`page_size×page_num ≤ 200`，超过返回空。若要超过 200 条，需要微调 keywords/types/radius 组合做多次检索后去重。
4. **show_fields 漏传 → 评分为空**：景点场景 99% 的情况都要加 `show_fields=business,photos`，否则 `rating/cost/opentime_week/photos` 都不返回。
5. **types 不要乱用 `05`/`07` 等餐饮类**：搜景点就传 `11` 开头；不传 types 会混进一堆餐厅。
6. **一次请求 keywords 只能 1 个词**：不能传"西湖,灵隐"两个，要查两个关键词得调用两次。
7. **QPS 与限流**：免费版通常 5,000 次/天，具体额度控制台"应用管理"可查；被限流返回 `infocode=10002`，此时退避重试。
8. **错误码参考**：<https://developer.amap.com/api/webservice/guide/tools/info>
9. **流量限制说明**：<https://developer.amap.com/api/webservice/guide/tools/flowlevel>

---

## 12. 在 Travel Assistant 项目中作为景点工具的用法建议

在后续的 Agent（Attractions Agent / Travel Planner）中，可以把高德 POI 搜索封装成 2~3 个 Tool：

| Tool | 入参 | 场景 |
|------|------|------|
| `amap_search_poi_by_keyword(keyword, region?, types?, city_limit?, page_size?)` | 关键词 + 可选城市 | 用户说"我想去北京的景点看看" |
| `amap_search_poi_around(lng, lat, radius_m?, keyword?, types?, sortrule?, page_size?)` | 经纬度 + 半径 | 用户说"我现在在上海外滩，附近有什么好玩的" |
| `amap_get_poi_detail(poi_id_list)` | POI ID（可批量≤20） | 展示景点详情：营业时间、门票、电话、入口坐标、图片 |

**给 LLM 的 Tool 描述要点**：
- 提示 GCJ-02 坐标
- 提示 `rating / cost / opentime_week` 需要 `show_fields=business`
- 提示最多返回 200 条、每页最多 25 条
- 景点 types 推荐默认值 `110000|110101|110200|110300`
