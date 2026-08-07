# 离线语料与运行报告 —— 设计文档

> 只定义设计，**不含实现**。写于 2026-08-07。
>
> 目标是为量化指标做准备。三件事互相依赖，所以放一篇：
> **录（存下真实响应）→ 放（默认离线跑）→ 记（每次运行产出 .md）**。
> 没有前两步，指标就不可复现；没有第三步，指标就没有载体。
>
> 相关：[系统架构](system-architecture.md) · [输出规格](output-spec.md)

---

## 0. 现状（读代码得到，非推测）

| 事实 | 位置 | 对本方案的意义 |
| --- | --- | --- |
| **SerpAPI 只有一个出口** | `providers/serpapi_client.py::SerpApiClient.search()` | 录制/回放只需挂这一处，不用到处埋点 |
| 高德同理 | `providers/amap_client.py::AmapClient.get()` | 同上 |
| **已有稳定的请求指纹** | `core/cache.py::make_key()`，dict 排序后 sha1 | 语料的文件名直接复用它，不用另发明一套 |
| 缓存键**不含 api_key** | `search()` 里 key 由 `params` 算，`api_key` 是之后才注入的 | 语料天然不含密钥，安全 |
| 已有替换客户端的口子 | `tools/registry.py::override_clients()` | 回放客户端可以从这里注入，不动业务代码 |
| 已有 9 份手工 fixture | `app/tests/fixtures/*.json` | 是**单元/契约测试**的资产，与本方案的语料库分开放 |
| 已有配额计数 | `core/metrics.py::record_call()` | 指标里"这次烧了多少额度"现成可用 |
| 测试靠 respx 拦截 | `app/tests/e2e/_mocks.py` | 那是**构造**的假数据，不是**录下来**的真数据，两者用途不同 |

---

## 1. 三种数据源，别混

| | 手工 fixture | **录制语料（新增）** | 真实 API |
| --- | --- | --- | --- |
| 来源 | 人写的 | 真实响应存下来的 | 线上 |
| 位置 | `app/tests/fixtures/` | `corpus/` | — |
| 用途 | 单元/契约测试的边界条件 | **跑指标、复现问题** | 补语料、验真 |
| 数量 | 9 份，手工维护 | 随用随增 | — |
| 进 git | 是 | **看情况，见 §6** | — |

手工 fixture 覆盖的是"字段缺失""脏数据""价格为 null"这类**刻意构造**的情况，
录制语料覆盖的是"真实世界长什么样"。**两者都要，不能互相替代**——
只有构造数据，会漏掉真实响应里的意外（比如「上海」返回法兰克福机场）；
只有录制数据，边界情况永远等不到。

---

## 2. 录：每次真实调用都存档

### 挂哪

`SerpApiClient.search()` 里，**紧接 `resp.json()` 之后、进缓存之前**。理由：

- 那时拿到的是**未经裁剪**的原始 payload（Tool 层的解析会丢字段，存裁剪后的等于自废武功）；
- 早于 `_raise_on_payload_error()`——**错误响应也要存**，它们恰恰是最该复现的样本；
- 缓存命中的路径不录（本来就没发生真实调用）。

高德同理挂在 `AmapClient.get()`。

### 存什么

```
corpus/
  serpapi/
    google_flights/
      2026-08-07T10-32-11__a3f9c1d20b4e7a85.json
    google_hotels/
    google_flights_autocomplete/
  amap/
    v5_place_text/
    v3_direction_transit/
  index.jsonl        # 一行一条，便于检索与统计
```

单条文件：

```jsonc
{
  "key": "serpapi:google_flights:a3f9c1d20b4e7a85",  // = make_key() 的结果
  "provider": "serpapi",
  "endpoint": "google_flights",
  "recorded_at": "2026-08-07T10:32:11+08:00",
  "request": { "engine": "google_flights", "departure_id": "PEK", "...": "..." },
  "response": { /* 原样，一字不改 */ },
  "http_status": 200,
  "elapsed_ms": 1840,
  "trip_id": "trp_..."          // 便于把一次规划的所有调用串起来
}
```

### 三条硬规矩

1. **`api_key` 绝不入库。** `search()` 是先算 key、后注入 `api_key` 的，
   录 `params` 而不是 `query` 就天然安全——但要有测试盯着这条线，
   密钥一旦写进语料再删就晚了（git 历史里追不回来）。
2. **录制失败不能影响业务。** 写盘出错只记 warning，绝不让一次规划因为
   存档失败而失败。这和 `_took_seconds` 那次教训是同一条：**可观测性不能反过来弄坏业务**。
3. **同 key 不覆盖，追加新时间戳文件。** 同一条查询隔一小时价格就变
   （实测 PEK→CTU 从 ¥4685/`3U 8890` 变成 ¥1927/`TV 9956`），
   覆盖等于把"数据会漂移"这个事实抹掉。回放时默认取**最新**的一份。

---

## 3. 放：默认离线，真实调用要显式开

新增一个运行模式开关：

```
UPSTREAM_MODE = replay | record | live
```

| 模式 | 行为 | 用途 |
| --- | --- | --- |
| **`replay`（默认）** | 只读 `corpus/`；查不到就报 `CORPUS_MISS` 错误，**不回落到真实 API** | 跑指标、日常开发 |
| `record` | 打真实 API，并把响应存进 `corpus/` | 补语料 |
| `live` | 打真实 API，不存档 | 临时排查 |

### 为什么 `replay` 查不到要报错而不是回落

**静默回落是最坏的设计**：你以为在跑离线基准，实际上偷偷打了真实 API，
指标不可复现，额度也莫名其妙地掉。`CORPUS_MISS` 必须显式失败，
错误信息里带上缺的 key 和补录命令：

```
CORPUS_MISS: 语料里没有 serpapi:google_flights:a3f9c1d2
补录：UPSTREAM_MODE=record python scripts/demo_trip.py 成都 --days 4
```

### 怎么实现（不动业务代码）

两个新的客户端类 `ReplaySerpApiClient` / `ReplayAmapClient`，接口与现有客户端一致，
通过已有的 `registry.override_clients()` 注入。**`app/providers/` 与 `app/tools/`
一行不改**——这正是"不删除 api 的代码"的落法：真实客户端原样保留，
只是默认不再被选中。

选择在哪发生：`tools/registry.py` 的 `serpapi_client()` / `amap_client()` 工厂里
按 `settings.upstream_mode` 决定 new 哪一个。

### 时间怎么办

录制的响应里日期是写死的（`outbound_date=2026-09-10`），而 demo 脚本默认
"30 天后出发"，隔一天就对不上，语料全部 miss。

**回放模式下把"今天"固定住**：新增 `FROZEN_TODAY`（默认取语料里最早的录制日期）。
`core/dates.py` 已经把 `today` 作为参数一路传下来了（`parse_relative_date(text, today)`），
所以只要在入口处注入这个固定值即可，不用改日期模块本身。

---

## 4. 记：每次运行产出一份 .md

### 产出到哪

```
runs/
  2026-08-07T10-32-11__成都__4天/
    report.md         # 人看的
    metrics.json      # 机器读的，喂给后续的指标聚合
    plan.json         # 完整 TripPlan 快照，便于 diff
```

一次运行一个目录，**目录名带时间戳和参数**，这样两次运行天然可比。

### report.md 长什么样

```markdown
# 成都 · 4 天 · 2026-09-10 → 2026-09-14

| | |
|---|---|
| 运行于 | 2026-08-07 10:32:11 |
| 上游模式 | replay（语料 2026-08-07） |
| 输入 | "9月10号从北京去成都玩4天，预算400一晚，想去都江堰" |
| 耗时 | 12.4s |

## 指标

| 指标 | 值 | 说明 |
|---|---|---|
| 全程通勤 | 1105 min | 越低越好 |
| 通勤/游玩比 | 1.42 | 目标 < 1.0 |
| 排入景点 | 11 / 20 | 备选 9 个 |
| 必去命中 | 1 / 1 | ✅ |
| 预算合规 | 8 / 8 家 ≤ ¥400 | ✅ |
| 酒店到重心 | 15 min | |
| 空白天数 | 1 | 航班时刻所限 |
| 配额 | SerpAPI 5 · 高德 29 · LLM 1 | |

## 行程
（逐日顺序，同终端输出）

## 告警
（PlanWarning 列表）

## 上游调用
| # | provider | endpoint | 命中 | key |
|---|---|---|---|---|
| 1 | serpapi | google_flights_autocomplete | 语料 | a3f9c1d2 |
```

### metrics.json 的意义

`report.md` 是给人看的，**指标聚合要读 `metrics.json`**——从 markdown 里
正则抠数字是自找麻烦。两份同源同时写，不允许只写一份。

### 什么时候写

`TripService` 在 `status` 进入终态时写（`done` 或 `failed`）。
**失败的运行也要出报告**——排不出行程恰恰是最需要量化的情况。

和录制一样：**写报告失败不影响业务**。

---

## 5. 指标口径（先定义，后实现）

分三类。**每个指标都要能从 `plan.json` 单独算出来**，不依赖运行时状态——
否则没法对历史运行回算。

### 5.1 行程质量

| 指标 | 定义 | 方向 |
| --- | --- | --- |
| `commute_min` | `Itinerary.total_commute_min` | ↓ |
| `commute_ratio` | 通勤分钟 / 游玩分钟 | ↓，目标 < 1.0 |
| `scheduled_ratio` | 排入景点数 / 入选景点数 | ↑ |
| `empty_days` | 没有任何景点的天数 | ↓ |
| `max_leg_min` | 单段最长通勤 | ↓，暴露"一天耗在路上" |

### 5.2 约束遵守（**布尔，最该先做**）

| 指标 | 定义 |
| --- | --- |
| `must_visit_hit` | 必去景点排入数 / 用户指定数，**应恒为 1.0** |
| `budget_ok` | 所有候选每晚价 ≤ `budget_per_night` |
| `dates_ok` | 行程首尾在落地时刻与返程起飞之间 |
| `airport_ok` | 候选机场全部属于目标城市 |

这一类是**回归护栏**：它们不该是"越高越好"，而是"低于 1 就是 bug"。
前几轮修过的每一个问题在这里都有对应项——机场混进法兰克福、
必去掉进备选、广告位突破预算，都会被这四个数字抓住。

### 5.3 成本

| 指标 | 定义 |
| --- | --- |
| `quota.serpapi` / `quota.amap` / `quota.llm` | `QuotaCounter`，已有 |
| `cache_hits` | 同上 |
| `wall_time_s` | 端到端耗时 |

---

## 6. 语料进不进 git

| | 进 | 不进 |
| --- | --- | --- |
| 好处 | 任何人 clone 下来就能跑指标；CI 可跑 | 仓库小 |
| 坏处 | 体积增长（单条 flights 响应 ~29 KB）；内容是第三方数据 | 换台机器就得重新录，指标不可复现 |

**建议：进 git，但设闸门。**

- 只收**基准集**（`corpus/baseline/`）：几条固定城市 × 固定日期的组合，人工挑选，是
  跑指标的最小可复现集合；
- 日常录制进 `corpus/scratch/`，**gitignore**；
- 基准集入库前必须过一遍脱敏检查（无 `api_key`、无 `search_metadata` 里的回链）。

⚠️ 第三方响应数据入库涉及 SerpAPI 的服务条款，**上线前要确认**。
在自用/研究范围内存档一般没问题，公开分发要另行判断。这条不是技术问题，
不要在代码里默默决定。

---

## 7. 影响面

| 改动 | 位置 | 性质 |
| --- | --- | --- |
| `UPSTREAM_MODE` / `FROZEN_TODAY` | `config.py` | 新增两个开关，默认值决定行为 |
| 录制钩子 | `providers/serpapi_client.py`、`amap_client.py` | 各插一行；**真实调用逻辑一行不改** |
| 回放客户端 | 新建 `app/corpus/`（读写语料 + Replay 客户端） | 全新模块 |
| 工厂按模式选客户端 | `tools/registry.py::serpapi_client()` | 改 3 行 |
| 报告生成 | 新建 `app/reports/` | 全新模块 |
| 触发写报告 | `services/trip_service.py` 终态处 | 加一次调用，异常吞掉 |
| 指标计算 | 新建 `app/reports/metrics.py`，纯函数吃 `TripPlan` | 纯函数，好测 |

**建议顺序**：录制 → 回放 → 报告 → 指标。
前两步做完，"不烧额度也能反复跑"就成立了，后两步才有意义。

---

## 8. 明确不做的

- **不删任何真实 API 代码。** 回放是**加一层**，不是替换。语料终究要靠
  `record` 模式去补，真实客户端永远是可用的。
- **不用 respx 做回放。** 那是测试期的传输层拦截，把它带进生产路径会让
  "现在到底走没走真网络"变得不可推理。回放客户端是显式的一个类，
  在工厂里明明白白地选中。
- **不复用 `TTLCache` 当语料库。** 缓存会过期、会 LRU 淘汰、只在内存里——
  三条特性没有一条是语料库要的。只复用它的 `make_key()`。
- **不做自动打分/排行榜。** 先把指标算准、可复现。什么算"好行程"是产品判断，
  不该由一个加权公式偷偷决定。
