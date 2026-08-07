"""端到端：intake → resolve_city → 航班分支 → attraction_search。

全部 provider 走 respx 假传输，不产生任何真实调用、不消耗任何额度。
重点验证失败路径会**立刻收尾**——每多跑一个节点就是多烧一次配额。
航班分支的中断/兜底单独在 test_flight_branch.py 里覆盖，本文件一律走 auto_select。
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest
import respx

from app.graph.builder import plan_trip
from app.models.errors import ErrorCode
from app.models.trip import TripRequest
from app.tests.e2e._mocks import (
    AROUND_URL,
    DETAIL_URL,
    DISTRICT_URL,
    TEXT_URL,
    district_payload,
    mock_downstream,
    mock_flights,
    outbound_payload,
    poi_payload,
    return_payload,
)


def make_request(**kw) -> TripRequest:
    base = {
        "departure_city": "北京",
        "destination_city": "杭州",
        # 用未来日期，避开 intake 的"出发日期已过"校验
        "outbound_date": date.today() + timedelta(days=30),
        "return_date": date.today() + timedelta(days=33),
        # 本文件不测中断，全自动跑完
        "auto_select": True,
    }
    return TripRequest(**{**base, **kw})


@pytest.fixture
def mock_apis():
    """一套能跑通全流程的高德 + SerpAPI 响应。"""
    with respx.mock:
        mock_downstream()
        mock_flights(date.today() + timedelta(days=30), date.today() + timedelta(days=33))
        respx.get(AROUND_URL).mock(
            return_value=httpx.Response(200, json=poi_payload(6, prefix="周边"))
        )
        yield


class TestHappyPath:
    async def test_runs_to_completion(self, mock_apis):
        state = await plan_trip(make_request())

        assert state["status"] == "done"
        assert state["phase"] == "done"
        assert not state["errors"]
        assert state["itinerary"] is not None
        assert state["summary"]  # 说明文案（LLM 不可用时由模板生成）

    async def test_city_is_resolved(self, mock_apis):
        state = await plan_trip(make_request())

        city = state["dest_city"]
        assert city.name == "杭州市"
        assert city.adcode == "330100"
        assert city.citycode == "0571"  # 公交换乘接口要用
        assert city.center.crs == "GCJ02"

    async def test_attractions_are_recalled_scored_and_selected(self, mock_apis):
        state = await plan_trip(make_request())

        branch = state["attractions"]
        # 两页 types-only 检索命中同一批 8 条（按 poi_id 去重）
        assert len(branch.pool) == 8
        # 4 天行程 → 上限 16，池子只有 8
        assert len(branch.selected) == 8
        assert branch.selected == sorted(branch.selected, key=lambda a: -a.score)
        assert branch.centroid is not None

    async def test_recall_ranks_follow_amap_ordering(self, mock_apis):
        # 高德 types-only 检索的返回顺序就是知名度排序，名次必须原样保留下来
        state = await plan_trip(make_request())
        by_id = {a.poi_id: a for a in state["attractions"].pool}
        assert by_id["景点-0"].recall_rank == 0
        assert by_id["景点-7"].recall_rank == 7

    @respx.mock
    async def test_recall_does_not_use_keyword_or_around_search(self):
        """回归保护：keywords=景点 会把排序毁掉，周边搜索只会返回行政中心附近的东西。

        杭州实测——types-only 给出 千岛湖/西湖/西溪/灵隐寺，keywords=景点 给出
        钱江世纪公园/清河坊/灯光秀。这个坑不能再踩回去。
        """
        request = make_request()
        mock_flights(request.outbound_date, request.return_date)
        respx.get(DISTRICT_URL).mock(return_value=httpx.Response(200, json=district_payload()))
        text = respx.get(TEXT_URL).mock(return_value=httpx.Response(200, json=poi_payload(8)))
        around = respx.get(AROUND_URL).mock(
            return_value=httpx.Response(200, json=poi_payload(6, prefix="周边"))
        )
        respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, json=poi_payload(0)))

        await plan_trip(request)

        assert around.call_count == 0
        for call in text.calls:
            assert "keywords" not in call.request.url.params
            assert call.request.url.params["types"].startswith("110000")

    async def test_locale_is_pinned_to_china(self, mock_apis):
        state = await plan_trip(make_request())
        loc = state["locale"]
        assert (loc.gl, loc.hl, loc.currency) == ("cn", "zh-CN", "CNY")

    async def test_must_visit_is_searched_separately_and_kept(self, mock_apis):
        state = await plan_trip(make_request(must_visit=["西湖"]))

        selected = state["attractions"].selected
        assert any(a.must_visit for a in selected)
        assert selected[0].must_visit  # 必去恒在最前

    async def test_quota_is_accounted(self, mock_apis):
        state = await plan_trip(make_request())

        quota = state["quota"]
        # 2 次机场补全 + 1 次去程 + 1 次返程 + 1 次酒店 = 5，这是每次规划的
        # SerpAPI 成本；免费额度 250/月 → 约 50 次规划，涨了要能一眼看出来
        assert quota.serpapi == 5
        assert quota.amap > 0

    async def test_short_pool_raises_a_warning(self, mock_apis):
        # 11 天行程需要 22 个景点，池子只有 14 条
        state = await plan_trip(make_request(return_date=date.today() + timedelta(days=40)))

        codes = {w.code for w in state["warnings"]}
        assert "FEW_ATTRACTIONS" in codes


class TestFailurePaths:
    @respx.mock
    async def test_past_departure_date_fails_before_any_network_call(self):
        district = respx.get(DISTRICT_URL).mock(
            return_value=httpx.Response(200, json=district_payload())
        )

        state = await plan_trip(
            make_request(
                outbound_date=date.today() - timedelta(days=1),
                return_date=date.today() + timedelta(days=3),
            )
        )

        assert state["status"] == "failed"
        assert state["errors"][0].code == ErrorCode.INVALID_PARAMS
        # 日期已过的请求发出去只会返回零结果，一次调用都不该发生
        assert district.call_count == 0

    @respx.mock
    async def test_unknown_city_stops_before_poi_search(self):
        respx.get(DISTRICT_URL).mock(
            return_value=httpx.Response(200, json={"status": "1", "districts": []})
        )
        text = respx.get(TEXT_URL).mock(return_value=httpx.Response(200, json=poi_payload(3)))

        state = await plan_trip(make_request(destination_city="不存在的城"))

        assert state["errors"][0].code == ErrorCode.CITY_NOT_FOUND
        assert text.call_count == 0

    @respx.mock
    async def test_hong_kong_is_rejected_as_out_of_coverage(self):
        respx.get(DISTRICT_URL).mock(
            return_value=httpx.Response(
                200,
                json=district_payload(
                    name="香港特别行政区", adcode="810000", citycode="", level="province"
                ),
            )
        )
        text = respx.get(TEXT_URL).mock(return_value=httpx.Response(200, json=poi_payload(3)))

        state = await plan_trip(make_request(destination_city="香港"))

        assert state["errors"][0].code == ErrorCode.DESTINATION_UNSUPPORTED
        assert "中国大陆" in state["errors"][0].user_message
        assert "adcode" not in state["errors"][0].user_message  # 技术细节不外泄
        assert text.call_count == 0

    @respx.mock
    async def test_province_is_rejected_as_too_broad(self):
        # 省份能定位但不能当目的地；错误信息要说清怎么改，而不是"没找到"
        respx.get(DISTRICT_URL).mock(
            return_value=httpx.Response(
                200,
                json=district_payload(
                    name="浙江省", adcode="330000", citycode="", level="province"
                ),
            )
        )
        text = respx.get(TEXT_URL).mock(return_value=httpx.Response(200, json=poi_payload(3)))

        state = await plan_trip(make_request(destination_city="浙江"))

        assert state["errors"][0].code == ErrorCode.DESTINATION_TOO_BROAD
        assert "杭州" in state["errors"][0].user_message
        assert text.call_count == 0

    @respx.mock
    async def test_empty_recall_fails_cleanly(self):
        request = make_request()
        mock_flights(request.outbound_date, request.return_date)
        respx.get(DISTRICT_URL).mock(return_value=httpx.Response(200, json=district_payload()))
        respx.get(TEXT_URL).mock(return_value=httpx.Response(200, json=poi_payload(0)))
        respx.get(AROUND_URL).mock(return_value=httpx.Response(200, json=poi_payload(0)))

        state = await plan_trip(request)

        assert state["status"] == "failed"
        assert state["errors"][0].code == ErrorCode.NO_ATTRACTIONS

    @respx.mock
    async def test_amap_outage_surfaces_as_upstream_error(self):
        respx.get(DISTRICT_URL).mock(return_value=httpx.Response(500))

        state = await plan_trip(make_request())

        assert state["status"] == "failed"
        assert state["errors"][0].code == ErrorCode.UPSTREAM_ERROR


class TestMunicipalities:
    @respx.mock
    async def test_beijing_is_recognised_despite_province_level(self):
        # 直辖市的 level 是 province，但 citycode 非空——真正的省份 citycode 为空
        # 出发地改上海：本文件默认从北京出发，目的地也是北京的话航线自相矛盾
        request = make_request(departure_city="上海", destination_city="北京")
        mock_downstream(
            district=district_payload(
                name="北京市", adcode="110000", citycode="010", level="province"
            ),
            pois=poi_payload(5),
        )
        mock_flights(
            request.outbound_date,
            request.return_date,
            departure_query="上海",
            departure_airports=[("SHA", "虹桥国际机场")],
            arrival_airports=[("PEK", "首都国际机场")],
            outbound_result=outbound_payload(
                request.outbound_date, count=1, dep_id="SHA", arr_id="PEK"
            ),
            return_result=return_payload(request.return_date, dep_id="PEK", arr_id="SHA"),
        )

        state = await plan_trip(request)

        assert state["status"] == "done"
        assert state["dest_city"].citycode == "010"
