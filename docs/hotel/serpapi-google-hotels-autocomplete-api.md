# SerpAPI Google Hotels Autocomplete API 工具文档

> 来源：<https://serpapi.com/google-hotels-autocomplete-api>（结合官方示例 playground）

---

## 1. 产品概述

Google Hotels Autocomplete 用于**在酒店搜索的"输入框补全"环节**：用户只输入部分酒店名、品牌名、城市名或关键词（如 `"day inn"`、`"Hilton Tokyo"`、`"hotel near times square"`），立即返回 Google Hotels 的匹配建议，包含：

- 品牌级建议（如 "Days Inn" → 可直接搜整个品牌的酒店）
- 单个酒店门店（带具体地址、thumbnail、property_token）
- 纯搜索词建议（如 `"day inn hotel near me"`、带 highlighted_words）

并且，**每条 suggestion 都自带 `serpapi_google_hotels_link`**（已预填 adults=2、check_in/out 默认日期、currency、gl、hl 等）和 `property_token`，**下一步直接转发给 `engine=google_hotels` 就能拿到酒店列表 / 单店房价**，形成一个完整的「输入补全 → 选酒店 → 查房价」链路。

**接口端点**：

```
GET https://serpapi.com/search.json?engine=google_hotels_autocomplete&q=...
```

或通过 `serpapi` 官方 SDK 的 `client.search({engine:"google_hotels_autocomplete", q:"..."})` 调用，内部自动走同一条链路。

Playground 在线演示：<https://serpapi.com/playground?engine=google_hotels_autocomplete>。

---

## 2. 原始代码 & JSON 示例（原封不动保留自官方）

> 下面 3 段是官方文档给出的 Python / Ruby / JSON，**原样照抄**，便于随时对照。

### 2.1 官方 Python 示例（SDK 调用）

```python
import serpapi

client = serpapi.Client(api_key="secret_api_key")
results = client.search({
  "engine": "google_hotels_autocomplete",
  "q": "New York"
})
suggestions = results["suggestions"]
```

> 本项目 `.env` 里已有 `SERPAPI_KEY`，代码里一律从环境变量读取（`settings.serpapi_key`），不要用文档里的 `secret_api_key`，也不要把真实 key 写进任何文件。

### 2.2 官方 cURL / GET URL

```
https://serpapi.com/search.json?engine=google_hotels_autocomplete&q=New+York
```

### 2.3 官方 Ruby 示例

```ruby
require "serpapi"

client = SerpApi::Client.new(
 engine: "google_hotels_autocomplete",
 q: "New York",
 api_key: "secret_api_key" )

results = client.search
suggestions = results[:suggestions]
```

### 2.4 官方 JSON 示例（`q=day inn`，节选 3 条最典型）

```json
{
  ...
  "search_metadata": { "id": "...", "status": "Success" },
  "search_parameters": {
    "engine": "google_hotels_autocomplete",
    "q": "day inn"
  },
  "suggestions": [
    {
      "position": 1,
      "value": "Days Inn",
      "type": "accommodation",
      "thumbnail": "https://encrypted-tbn1.gstatic.com/images?q=tbn:ANd9GcRdCsoF2mNt1Bgzy5bZgkeSItkGy2pa4KLGJ9eP-3jzKoRWLor-",
      "autocomplete_suggestion": "day inn",
      "kgmid": "/m/04558f",
      "serpapi_google_hotels_link": "https://serpapi.com/search.json?adults=2&check_in_date=2026-07-17&check_out_date=2026-07-18&children=0&currency=USD&engine=google_hotels&gl=us&hl=en",
      "serpapi_link": "https://serpapi.com/search.json?device=desktop&engine=google&google_domain=google.com&kgmid=%2Fm%2F04558f&q=Days+Inn"
    },
    {
      "position": 4,
      "value": "Days Inn by Wyndham Washington DC/Connecticut Avenue",
      "type": "accommodation",
      "location": "4400 Connecticut Ave NW, Washington",
      "thumbnail": "https://encrypted-tbn0.gstatic.com/images?q=tbn:ANd9GcRGMMa8_GQe-HgeegrfQuGBnok24ALfKcDoFdEjtHU2INv7eCcI",
      "highlighted_words": [
        "washington",
        "dc"
      ],
      "autocomplete_suggestion": "day inn washington dc",
      "kgmid": "/g/1hf8_s33q",
      "data_cid": "242587712928378716",
      "property_token": "ChgI3N7hv9WH9q4DGgwvZy8xaGY4X3MzM3EQAQ",
      "serpapi_google_hotels_link": "https://serpapi.com/search.json?adults=2&check_in_date=2026-07-17&check_out_date=2026-07-18&children=0&currency=USD&engine=google_hotels&gl=us&hl=en&property_token=ChgI3N7hv9WH9q4DGgwvZy8xaGY4X3MzM3EQAQ&q=4400+Connecticut+Ave+NW%2C+Washington",
      "serpapi_link": "https://serpapi.com/search.json?device=desktop&engine=google&google_domain=google.com&kgmid=%2Fg%2F1hf8_s33q&q=Days+Inn+by+Wyndham+Washington+DC%2FConnecticut+Avenue"
    },
    {
      "position": 6,
      "value": "day inn hotel near me",
      "type": "accommodation",
      "highlighted_words": [
        "hotel",
        "near",
        "me"
      ],
      "autocomplete_suggestion": "day inn hotel near me",
      "serpapi_google_hotels_link": "https://serpapi.com/search.json?adults=2&check_in_date=2026-07-17&check_out_date=2026-07-18&children=0&currency=USD&engine=google_hotels&gl=us&hl=en&q=day+inn+hotel+near+me"
    }
  ]
}
```

---

## 3. 请求参数全表

### 3.1 Search Query（搜索核心）

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `q` | ✅ | 补全关键词。用户在输入框里敲什么就传什么（Google Hotels 会做拼写纠错+模糊匹配）。可以是品牌名 `"Hilton"` / 具体门店名 `"Hilton Tokyo Bay"` / 城市 `"New York hotel"` / 带意图的搜索词 `"cheap hotel in paris near eiffel tower"`。 |

### 3.2 Localization（本地化）

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `gl` | 否 | 两位国家代码，`us`=美国、`uk`=英国、`fr`=法国、`cn`=中国。影响返回的品牌是否本地化、currency 默认值、酒店排序。支持列表：<https://serpapi.com/google-countries> |
| `hl` | 否 | 两位语言代码，`en`/`zh-CN`/`ja`/`es`/`fr`… 影响 suggestion 的 value 语言。支持列表：<https://serpapi.com/google-languages> |
| `currency` | 否 | 影响 `serpapi_google_hotels_link` 里预填的币种。默认 `USD`；常用 `CNY`、`EUR`、`GBP`、`JPY`。完整币种：<https://serpapi.com/google-travel-currencies> |

### 3.3 SerpAPI 通用参数

| 参数 | 必填 | 说明 |
|------|:----:|------|
| `engine` | ✅ | 固定 `google_hotels_autocomplete` |
| `api_key` | ✅ | SerpAPI Key，本项目 `.env` 里 `SERPAPI_KEY` |
| `no_cache` | 否 | `true`=绕过 1 小时缓存（会扣额度）；默认 `false`=优先用缓存，缓存免费不扣额度 |
| `async` | 否 | `true`=异步提交，之后要去 Searches Archive API 取结果；默认 `false`=同步等待结果直到返回。**不要和 no_cache=true 同时开**。 |
| `zero_trace` | 否 | 企业版专属，`true`=不存搜索参数/文件（更难调试），默认 `false` |
| `output` | 否 | `json`(默认) / `html`(仅文本)。建议用 json。 |

---

## 4. 返回字段详解

所有字段都在顶层 `suggestions: Suggestion[]` 数组里。

### 4.1 Suggestion 字段总览

官方 JSON structure overview：

```json
{
  "suggestions": [
    {
      "position": 1,
      "value": "Days Inn by Wyndham Washington DC/Connecticut Avenue",
      "type": "accommodation",
      "location": "4400 Connecticut Ave NW, Washington",
      "thumbnail": "https://...",
      "highlighted_words": ["washington","dc"],
      "autocomplete_suggestion": "day inn washington dc",
      "kgmid": "/g/1hf8_s33q",
      "data_cid": "242587712928378716",
      "property_token": "ChgI3N7hv9WH9q4DGgwvZy8xaGY4X3MzM3EQAQ",
      "serpapi_google_hotels_link": "https://serpapi.com/search.json?adults=2&...",
      "serpapi_link": "https://serpapi.com/search.json?device=desktop&engine=google&..."
    }
  ]
}
```

逐字段说明：

| 字段 | 出现频率 | 类型 | 说明 & 在 Agent 里的用法 |
|------|:--------:|------|--------------------------|
| `position` | ✅ 总有 | int | 1-based 排序号。Google 认为最相关的在 position=1。给用户展示时保持原顺序即可。 |
| `value` | ✅ 总有 | string | **展示给用户看的主文本**。要么是品牌名（`Days Inn`）、要么是完整酒店门店名 + 品牌、要么是搜索词。 |
| `type` | ✅ 总有 | string | 目前只见过 `"accommodation"`。未来可能扩展。用于做 icon（🏨）。 |
| `location` | ⭕ 门店级才有 | string | **门店才有的具体地址**（街道+城市，通常是酒店前台登记地址）。用户选择时优先展示给"想订这家具体门店"的场景。 |
| `thumbnail` | ⭕ 品牌/门店才有 | string | 酒店缩略图 URL（通常是 Google 的 `encrypted-tbn*.gstatic.com`）。给卡片式 UI 用，纯对话 Agent 可以不展示。 |
| `highlighted_words[]` | ⭕ 部分有 | string[] | 用户输入中被高亮命中的词（小写）。可用来在回复中把命中词加粗。 |
| `autocomplete_suggestion` | ✅ 总有 | string | **当用户选择这条 suggestion 时，下一步要交给 `engine=google_hotels` 的 `q`**（通常是 value 被标准化后的版本）。优先用此字段而不是用户原输入。 |
| `kgmid` | ⭕ 品牌/门店才有 | string | Google 知识图谱 ID（`/m/...`、`/g/...`）。如果后续要走 Google Search 的 `kgmid` 参数，用它；也可用作去重键。 |
| `data_cid` | ⭕ 门店级才有 | string | 也叫 `ludocid`，Google 本地商家/地点 CID。对单门店唯一性比 kgmid 更强。 |
| `property_token` | ⭕ 门店级才有 | string | **单酒店查房价最重要的字段**。当用户明确"就想订这一家 Washington DC / Connecticut Ave 的 Days Inn"时，把 `property_token + q(地址)` 直接传给 `engine=google_hotels`，Google 会直接返回这一家的房型/价格列表，而不是一堆结果。 |
| `serpapi_google_hotels_link` | ✅ 总有 | string | 已经拼装好的、可以直接 `requests.get()` 的 `engine=google_hotels` URL（已经预填了默认的 adults/children/check_in/out/currency/gl/hl，门店还带 property_token+q）。**最省心的下一步做法**：要么直接 urllib.parse.parse_qs 把 query 拆出来重构成 google_hotels 的调用参数；要么在非生产环境直接 GET 这个链接（注意已包含 api_key）。 |
| `serpapi_link` | ⭕ 品牌/门店才有 | string | 对应 `engine=google` 的搜索 URL（用 kgmid 搜这酒店的 Google 网页卡片结果）。Travel Assistant 一般用不到。 |

### 4.2 三类 Suggestion 的字段出现模式（Agent 判断分支参考）

| 类别 | value 样例 | 必带字段 | 典型缺的字段 | Agent 如何展示 |
|------|-----------|----------|--------------|----------------|
| **A. 品牌级** | `"Days Inn"` `"Hilton"` | position, value, type, thumbnail, autocomplete_suggestion, kgmid, serpapi_google_hotels_link, serpapi_link | **location、data_cid、property_token** | 「🏨 品牌：Days Inn（连锁）· 下一步搜该品牌的所有酒店」 |
| **B. 单门店级** | `"Days Inn by Wyndham Washington DC/Connecticut Avenue"` | 全部字段，包括 **location, data_cid, property_token** | — | 「🏨 Days Inn by Wyndham… 📍 4400 Connecticut Ave NW, Washington · 可以直接看这家的房价」（**property_token 存在时，优先走单酒店详情**） |
| **C. 搜索词建议** | `"day inn hotel near me"` `"cheap motel in tokyo"` | position, value, type, highlighted_words, autocomplete_suggestion, serpapi_google_hotels_link | kgmid / location / data_cid / property_token / thumbnail 等 | 「💡 推荐搜索词：day inn hotel near me · 按此搜索」 |

> **对 Agent 的核心决策**：只要看到返回的 suggestion 里有 `property_token`，就代表这是一个**具体酒店**；用户点/说选它之后，`google_hotels` 直接带 `property_token` + `q` 查单店房价；如果没有 property_token（品牌或搜索词），就走常规 `q=autocomplete_suggestion` 的酒店列表搜索。

---

## 5. Python 完整工具示例（读取本项目 .env）

```python
"""
SerpAPI Google Hotels Autocomplete 工具
依赖: pip install serpapi python-dotenv requests
"""
import os
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse, parse_qs

import serpapi
from dotenv import load_dotenv

load_dotenv()
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")


SuggestionKind = Literal["brand", "property", "search_term"]


@dataclass
class Suggestion:
    position: int
    value: str
    kind: SuggestionKind
    autocomplete_suggestion: str
    location: str | None
    thumbnail: str | None
    kgmid: str | None
    data_cid: str | None
    property_token: str | None
    serpapi_google_hotels_link: str
    highlighted_words: list[str]

    @property
    def display_title(self) -> str:
        loc = f"📍 {self.location}" if self.location else ""
        tag = {
            "brand": "🏨 [品牌]",
            "property": "🏨 [酒店]",
            "search_term": "💡 [搜索]",
        }[self.kind]
        return f"{tag} {self.value} {loc}".rstrip()


def _classify(sugg: dict) -> SuggestionKind:
    if sugg.get("property_token"):
        return "property"
    if sugg.get("kgmid") and not sugg.get("highlighted_words") and " " not in (sugg.get("value") or ""):
        # 纯品牌名（单字/非常短的 value + kgmid + 没高亮用户搜索词）
        return "brand"
    # 剩下的当搜索词建议（"xxx near me" / "cheap xxx"）
    # 注意：少数品牌连锁 value 含空格但也是"品牌级"，可自行调优规则
    if sugg.get("kgmid"):
        return "brand"
    return "search_term"


def google_hotels_autocomplete(
    q: str,
    gl: str | None = None,
    hl: str | None = "en",
    currency: str = "USD",
) -> list[Suggestion]:
    """
    调用 Google Hotels Autocomplete，返回结构化 Suggestion 列表。
    :param q: 用户输入关键词（推荐直接透传用户原输入，不要预处理）
    :param gl: 国家代码 us / cn / jp / fr ...
    :param hl: 语言代码 en / zh-CN / ja ...
    :param currency: USD / CNY / EUR ...
    """
    if not SERPAPI_KEY:
        raise RuntimeError("缺少 SERPAPI_KEY，请先在 .env 填入")

    params = {
        "engine": "google_hotels_autocomplete",
        "q": q,
        "currency": currency,
    }
    if gl: params["gl"] = gl
    if hl: params["hl"] = hl

    # 用官方 SDK（内部自动走 search.json + api_key）
    client = serpapi.Client(api_key=SERPAPI_KEY)
    raw = client.search(params)

    out: list[Suggestion] = []
    for s in raw.get("suggestions", []) or []:
        out.append(Suggestion(
            position=int(s.get("position", 0)),
            value=s.get("value", ""),
            kind=_classify(s),
            autocomplete_suggestion=s.get("autocomplete_suggestion") or s.get("value", ""),
            location=s.get("location"),
            thumbnail=s.get("thumbnail"),
            kgmid=s.get("kgmid"),
            data_cid=s.get("data_cid"),
            property_token=s.get("property_token"),
            serpapi_google_hotels_link=s.get("serpapi_google_hotels_link", ""),
            highlighted_words=s.get("highlighted_words") or [],
        ))
    out.sort(key=lambda x: x.position)
    return out


def extract_hotels_params_from_link(link: str) -> dict:
    """
    工具函数：从 serpapi_google_hotels_link 里反解出下次调用 google_hotels engine 要的参数 dict。
    这样就不用手动改 q / property_token / check_in / adults 了。
    """
    query = urlparse(link).query
    params = {k: v[0] if len(v) == 1 else v for k, v in parse_qs(query).items()}
    # 去掉 serpapi 自动加的、非 google_hotels 必传的字段
    params.pop("engine", None)  # 外部调用时再自行加 engine=google_hotels
    return params


def _clean_markdown(s: str) -> str:
    return re.sub(r"[|]", "｜", s)


if __name__ == "__main__":
    # 示例 1：用户输入 "day inn"，看有哪些建议
    results = google_hotels_autocomplete("day inn", gl="us", currency="USD")
    print("===== q='day inn' =====")
    for r in results:
        print(f"{r.position}. {r.display_title}")
        token = f"  property_token={r.property_token[:16]}…" if r.property_token else ""
        kw = f"  下一步q={r.autocomplete_suggestion!r}"
        print(token or kw)

    # 示例 2：用户选择了"第 4 条门店（华盛顿那家 Days Inn）"，直接拿下次 google_hotels 的参数
    prop_item = next((r for r in results if r.kind == "property"), None)
    if prop_item:
        print("\n===== 选第 4 条门店后，传给 google_hotels 的参数 =====")
        next_params = extract_hotels_params_from_link(prop_item.serpapi_google_hotels_link)
        for k, v in next_params.items():
            if len(str(v)) > 80:
                v = str(v)[:80] + "…"
            print(f"  {k}: {_clean_markdown(str(v))}")
```

---

## 6. 在 Travel Assistant 中作为 Hotel Agent Tool 的设计建议

这个 Autocomplete 是 **Hotel Booking Agent 的第一个 Tool**，和 Flight Agent 里的 `google_flights_autocomplete` 地位完全对应，推荐直接封装成：

### 6.1 Tool: `serpapi_google_hotels_autocomplete`

#### Tool Schema

```json
{
  "name": "serpapi_google_hotels_autocomplete",
  "description": "当用户想搜索酒店、提供了模糊的城市/酒店品牌名/部分门店名（如 '东京的 Hilton'、'上海迪士尼附近的酒店'、'day inn'），或输入不完整时，请首先调用此工具。它调用 SerpAPI 的 engine=google_hotels_autocomplete，返回 3~10 条候选建议。每条建议包含：position(排序)、value(展示名)、location(具体门店地址，有就代表单酒店)、autocomplete_suggestion(下一步 google_hotels 的 q)、property_token(有就代表可直接查这一家的房价)、highlighted_words、serpapi_google_hotels_link(下一步 google_hotels 的预填 URL)。注意：用户选择建议后，如果建议有 property_token，下一步调用 google_hotels 时要带 property_token 才能定位到具体酒店；没有 property_token 的建议（品牌/搜索词）下一步传 q=autocomplete_suggestion 做普通酒店列表搜索。",
  "parameters": {
    "type": "object",
    "properties": {
      "q":        { "type": "string", "description": "用户输入的酒店搜索关键词，原样透传，不要改大小写不要纠错" },
      "gl":       { "type": "string", "description": "可选，两位国家代码：us=美国 cn=中国 jp=日本 fr=法国。能从上下文判断就填上" },
      "hl":       { "type": "string", "description": "可选，两位语言代码，默认 en，面向中文用户时填 zh-CN" },
      "currency": { "type": "string", "description": "币种 ISO 代码，默认 USD；对中国用户可填 CNY，日本 JPY" }
    },
    "required": ["q"]
  }
}
```

### 6.2 典型 Agent 交互流程（对应 Flight Agent 的机场选择）

```
User: "我想在纽约找一个希尔顿的酒店"
  ↓
Thought: 用户提供了模糊目的地+品牌名，需要先做 Hotels Autocomplete 让用户在具体门店/品牌/搜索词里选。
Action: serpapi_google_hotels_autocomplete
Action Input: {"q":"New York Hilton","gl":"us","hl":"en","currency":"USD"}
  ↓
Observation (suggestions 示例):
  1. 🏨 [品牌]  Hilton
  2. 🏨 [酒店]  Hilton New York Midtown             📍 100 W 54th St, New York   property_token=xxx…
  3. 🏨 [酒店]  Hilton Times Square                 📍 234 W 42nd St, New York   property_token=yyy…
  4. 💡 [搜索]  hilton hotel new york times square
  ↓
Agent Response:
  为您找到以下 "New York Hilton" 相关选项，请选择：
  1. 🏨 品牌 - Hilton（希尔顿连锁，下一步搜纽约所有希尔顿）
  2. 🏨 Hilton New York Midtown · 📍 100 W 54th St, New York（可直接看这家房价）
  3. 🏨 Hilton Times Square · 📍 234 W 42nd St, New York（可直接看这家房价）
  4. 💡 或者使用推荐搜索词："hilton hotel new york times square"
  ↓
User: "就选第 3 家 Times Square 那家"
  ↓
Thought: 用户选了第 3 条，是单门店（有 property_token）。下一步应该调用 google_hotels engine，传入从 serpapi_google_hotels_link 反解出来的参数，再覆盖真实的入住/离店日期、人数。
→ 进入下一阶段：询问/确认日期、人数、房型偏好，最后触发 google_hotels（单店）搜索。
```

---

## 7. 注意事项 & 坑

1. **serpapi_google_hotels_link 默认填的是"今天 + 1 晚"的日期**。不要直接复用日期！用户要订的日期几乎肯定不同。正确做法：先用 `extract_hotels_params_from_link` 把参数解出来，然后 **覆盖** `check_in_date`、`check_out_date`、`adults`、`children`（以及带儿童时的 `children_ages`）、可选的 `sort_by` / `min_price` / `max_price` 等。
2. **currency / gl / hl 的一致性很重要**。Autocomplete 用了 `currency=CNY`，下一步的 `google_hotels` 就也要 `currency=CNY`，否则价格换算会让用户困惑；本项目里建议这三个参数在 AgentState 里存一份全局设置（用户首次搜索时设置一次，后面一路复用）。
3. **property_token 是单酒店的钥匙，不要自己拼**。正确来源永远是 Autocomplete 返回的 suggestion；不要用 kgmid/data_cid 替代 property_token 去请求 google_hotels，容易搜到隔壁同名店。
4. **highlighted_words 用来高亮**。展示给用户时，把 `highlighted_words` 中每个词在 value 里找到对应的 span 做加粗，用户体验更好。
5. **一个 q 不一定三种 suggestion 都有**。例如 `"Hilton Tokyo Bay"` 大概率返回 2 条品牌 + 5 条门店 + 1 个搜索词；而 `"oijoij random"` 可能返回 0 条 suggestions。需做容错：空列表就提示"没找到匹配酒店，试试换个拼写、去掉品牌词，或者写完整地址"。
6. **cache 默认 1 小时免费**。同一参数不要反复开 `no_cache=true`，会把额度烧完。生产环境建议 `no_cache=false`（默认），只有在"同一关键词结果已经变旧"时才强制刷新。
7. **额度**：免费版 250 次/月（不管是否命中缓存，开 no_cache 或首次调用的一次算一次）。Travel Assistant 真实用量会快速超，建议升级到 Hobby / Pro。
8. **zero_trace 影响调试**。遇到返回字段不完整/异常时，先不要开 zero_trace，以便 SerpAPI 支持团队根据 `search_metadata.id` 回溯。
9. **`type` 字段目前几乎全是 accommodation**。不要依赖 type 去区分品牌/门店/搜索词；用第 4.2 节的字段出现模式（是否有 property_token、kgmid、location、highlighted_words 组合）更可靠。
