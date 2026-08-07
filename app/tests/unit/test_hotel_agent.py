"""酒店重排与锚点构造测试（纯函数，不碰网络）。"""

from __future__ import annotations

import pytest

from app.agents.hotel_agent import (
    MIN_ORGANIC_ASKED,
    NEUTRAL,
    TOP_N,
    W_COMMUTE,
    W_PRICE,
    W_RATING,
    build_query,
    drop_over_budget,
    nights_between,
    pick_options,
    rerank_hotels,
)
from app.models.attraction import Attraction
from app.models.common import CityRef, GeoPoint
from app.models.hotel import HotelCandidate, Rate, price_text

CITY = CityRef(
    name="杭州市", adcode="330100", citycode="0571", center=GeoPoint.gcj02(120.21, 30.25)
)


def attraction(name: str, area: str = "") -> Attraction:
    return Attraction(
        poi_id=name, name=name, location=GeoPoint.gcj02(120.15, 30.24), business_area=area
    )


def hotel(
    name: str,
    *,
    total: float | None = None,
    nightly: float | None = None,
    rating: float | None = None,
    commute: int | None = None,
    is_ad: bool = False,
    token: str = "",
) -> HotelCandidate:
    return HotelCandidate(
        name=name,
        property_token=token or name,
        is_ad=is_ad,
        overall_rating=rating,
        total_rate=Rate(lowest=f"¥{total:.0f}", extracted_lowest=total) if total else None,
        rate_per_night=Rate(lowest=f"¥{nightly:.0f}", extracted_lowest=nightly)
        if nightly
        else None,
        location=GeoPoint.wgs84(120.15, 30.24),
        commute_to_centroid_min=commute,
    )


class TestBuildQuery:
    def test_uses_the_dominant_business_area(self):
        # 「杭州市西湖景区附近酒店」比「杭州市酒店」命中率高得多
        attractions = [
            attraction("断桥", "西湖景区"),
            attraction("雷峰塔", "西湖景区"),
            attraction("宋城", "之江"),
        ]
        assert build_query(CITY, attractions) == "杭州市西湖景区附近酒店"

    def test_falls_back_to_the_city_when_no_area_is_known(self):
        assert build_query(CITY, [attraction("某景点")]) == "杭州市酒店"

    def test_handles_empty_attraction_list(self):
        assert build_query(CITY, []) == "杭州市酒店"


class TestRerank:
    def test_cheaper_wins_all_else_equal(self):
        ranked = rerank_hotels(
            [hotel("贵", total=3000, rating=4.5, commute=20),
             hotel("便宜", total=1000, rating=4.5, commute=20)],
            nights=3,
        )
        assert ranked[0].name == "便宜"

    def test_better_rating_wins_all_else_equal(self):
        ranked = rerank_hotels(
            [hotel("差评", total=2000, rating=3.0, commute=20),
             hotel("好评", total=2000, rating=4.9, commute=20)],
            nights=3,
        )
        assert ranked[0].name == "好评"

    def test_closer_to_the_attractions_wins_all_else_equal(self):
        # 这是本模块存在的意义：Google 的原生排序不知道用户要去哪些景点
        ranked = rerank_hotels(
            [hotel("远", total=2000, rating=4.5, commute=55),
             hotel("近", total=2000, rating=4.5, commute=5)],
            nights=3,
        )
        assert ranked[0].name == "近"

    def test_commute_outweighs_price_at_the_configured_weights(self):
        """通勤 0.55 压过价格 0.27。

        实测成都：旧配比（价格 0.45 / 通勤 0.25）把一家 ¥100/晚、到景点重心
        **75 分钟**的酒店排到第一，34 分钟那家反而第五。省下的钱远不够补
        每天多花两小时通勤。
        """
        ranked = rerank_hotels(
            [hotel("便宜但远", total=1000, rating=4.5, commute=60),
             hotel("贵但近", total=3000, rating=4.5, commute=0)],
            nights=3,
        )

        assert ranked[0].name == "贵但近"
        assert W_COMMUTE > W_PRICE + W_RATING  # 通勤单项就能翻盘

    def test_the_weights_still_sum_to_one(self):
        # 三项各自 min-max 归一到 [0,1]，权重不加满 1 的话总分尺度会漂
        assert W_PRICE + W_RATING + W_COMMUTE == pytest.approx(1.0)

    def test_price_still_breaks_ties_when_commute_is_equal(self):
        # 通勤压倒不等于价格失效
        ranked = rerank_hotels(
            [hotel("贵", total=3000, rating=4.5, commute=20),
             hotel("便宜", total=1000, rating=4.5, commute=20)],
            nights=3,
        )

        assert ranked[0].name == "便宜"

    def test_nightly_price_is_multiplied_by_nights_when_total_is_missing(self):
        # ads 只给单晚价；不折算成总价就没法和 organic 的 total_rate 比
        ranked = rerank_hotels(
            [hotel("单晚300住3晚", nightly=300, rating=4.5),
             hotel("总价1200", total=1200, rating=4.5)],
            nights=3,
        )
        assert ranked[0].name == "单晚300住3晚"  # 900 < 1200

    def test_missing_price_scores_between_cheapest_and_dearest(self):
        # 高德降级来源没有房价，不该因此被判死刑，也不该白捡便宜
        ranked = rerank_hotels(
            [
                hotel("便宜", total=1000, rating=4.5, commute=20),
                hotel("很贵", total=9000, rating=4.5, commute=20),
                hotel("无价", rating=4.5, commute=20),
            ],
            nights=3,
        )
        assert [c.name for c in ranked] == ["便宜", "无价", "很贵"]

    def test_a_lone_priced_candidate_cannot_be_judged_expensive(self):
        # 只有一个报价时无从比较，min==max，两者都拿中位分——这是对的，
        # "9000 贵不贵"在没有参照系时无法判断
        ranked = rerank_hotels(
            [hotel("有价", total=9000, rating=4.5, commute=20),
             hotel("无价", rating=4.5, commute=20)],
            nights=3,
        )
        assert ranked[0].score == pytest.approx(ranked[1].score)

    def test_identical_candidates_all_get_the_neutral_price_score(self):
        ranked = rerank_hotels(
            [hotel("A", total=2000, rating=5.0, commute=0),
             hotel("B", total=2000, rating=5.0, commute=0)],
            nights=3,
        )
        # 价格全相同 → 归一化会除零，必须退回中位分而不是崩掉
        expected = W_PRICE * NEUTRAL + W_RATING * 1.0 + W_COMMUTE * 1.0
        assert ranked[0].score == pytest.approx(expected, abs=1e-4)

    def test_commute_beyond_the_cap_saturates(self):
        far = rerank_hotels([hotel("很远", total=2000, rating=4.5, commute=120)], nights=3)[0]
        cap = rerank_hotels([hotel("到顶", total=2000, rating=4.5, commute=60)], nights=3)[0]
        assert far.score == pytest.approx(cap.score)

    def test_returns_at_most_top_n(self):
        pool = [hotel(f"H{i}", total=1000 + i * 100, rating=4.5) for i in range(20)]

        # 候选多不花额度：attach_commute 用 distance_batch 一次覆盖 100 个起点
        assert len(rerank_hotels(pool, nights=3)) == TOP_N

    def test_fewer_candidates_than_top_n_are_all_kept(self):
        pool = [hotel(f"H{i}", total=1000 + i * 100, rating=4.5) for i in range(3)]

        assert len(rerank_hotels(pool, nights=3)) == 3

    def test_empty_input(self):
        assert rerank_hotels([], nights=3) == []


def test_nights_between_never_returns_zero():
    from datetime import date

    assert nights_between(date(2026, 8, 10), date(2026, 8, 13)) == 3
    # 同日入住离店在 TripRequest 层已被挡掉，这里兜底成 1 而不是 0（会导致除零）
    assert nights_between(date(2026, 8, 10), date(2026, 8, 10)) == 1


class TestPriceText:
    """房价一律「每晚价 · 共总价」两个都给。

    只印一个，候选之间就没法比：实测成都 2026-08-06，第 1 行印「总价 ¥301」、
    第 2 行印「¥190/晚」，看着像 301 比 190 贵，其实前者每晚才 ¥100。
    """

    def test_total_wins_and_nightly_is_derived_from_it(self):
        # rate_per_night 是"起价"，total_rate 才是这一单实际要付的钱。
        # 两个都原样并排会自相矛盾：4 晚却写着「¥400/晚 · 共 ¥1200」
        assert price_text(total=1200.0, nightly=400.0, nights=4) == "¥300/晚 · 共 ¥1200"

    def test_nightly_only_estimates_the_total(self):
        # ads 只给单晚价
        assert price_text(total=None, nightly=190.0, nights=3) == "¥190/晚 · 共约 ¥570"

    def test_the_chengdu_case_is_comparable_again(self):
        cheap = price_text(total=301.0, nightly=100.0, nights=3)   # organic
        pricey = price_text(total=None, nightly=190.0, nights=3)   # ad

        assert cheap.startswith("¥100/晚")
        assert pricey.startswith("¥190/晚")  # 同口径，一眼能比

    def test_missing_prices_are_not_zero(self):
        assert price_text(total=None, nightly=None, nights=3) == "价格暂无"

    def test_zero_nights_does_not_divide_by_zero(self):
        assert price_text(total=500.0, nightly=None, nights=0) == "¥500/晚 · 共 ¥500"


class TestBudgetFilter:
    """`--budget` 是**每晚**上限，得对所有来源生效。

    SerpAPI 的 `max_price` 只作用于 organic 结果，`ads[]` 不受约束——实测成都
    `--budget 500` 依然返回 ¥725/晚 的广告位，还因为离景点近被排到了第一。
    """

    def test_an_over_budget_ad_is_dropped(self):
        kept, warning = drop_over_budget(
            [hotel("超预算广告", nightly=725, is_ad=True, token="a"),
             hotel("合规", nightly=300, token="b")],
            budget_per_night=500,
            nights=4,
        )

        assert [c.name for c in kept] == ["合规"]
        assert warning is None

    def test_total_only_candidates_are_judged_per_night(self):
        # organic 只给 total_rate，要折算成每晚价才能和上限比
        kept, _ = drop_over_budget(
            [hotel("四晚2800", total=2800, token="a"),   # ¥700/晚，超
             hotel("四晚1200", total=1200, token="b")],  # ¥300/晚，可以
            budget_per_night=500,
            nights=4,
        )

        assert [c.name for c in kept] == ["四晚1200"]

    def test_a_candidate_without_a_price_is_kept(self):
        # 高德降级来源没有房价；无从判断，不能当超预算处理
        kept, _ = drop_over_budget(
            [hotel("无价", token="a")], budget_per_night=500, nights=4
        )

        assert len(kept) == 1

    def test_no_budget_means_no_filtering(self):
        pool = [hotel("很贵", nightly=9999, token="a")]

        assert drop_over_budget(pool, None, nights=4)[0] == pool

    def test_filtering_everything_out_falls_back_with_a_warning(self):
        # 给一份超预算的候选，好过给空
        pool = [hotel("贵A", nightly=900, token="a"), hotel("贵B", nightly=800, token="b")]

        kept, warning = drop_over_budget(pool, budget_per_night=100, nights=4)

        assert kept == pool
        assert warning is not None
        assert warning.code == "HOTEL_OVER_BUDGET"

    def test_exactly_at_the_cap_is_allowed(self):
        kept, _ = drop_over_budget(
            [hotel("刚好", nightly=500, token="a")], budget_per_night=500, nights=4
        )

        assert len(kept) == 1


class TestPickOptions:
    """只列广告位等于没得选——它们是付费展位，不是"最合适的酒店"。

    实测成都 8 家候选里 6 家是广告，前 4 名全被占满。
    """

    def _pool(self, ads: int, organic: int) -> list[HotelCandidate]:
        # 分数从高到低：广告在前，模拟"广告把前几名占满"
        pool = [hotel(f"广告{i}", nightly=200, is_ad=True, token=f"a{i}") for i in range(ads)]
        pool += [hotel(f"普通{i}", nightly=200, token=f"o{i}") for i in range(organic)]
        for i, c in enumerate(pool):
            c.score = 1.0 - i * 0.01
        return pool

    def test_non_ads_get_a_seat_even_when_ads_score_higher(self):
        asked = pick_options(self._pool(ads=6, organic=2), ask_n=4)

        assert len([c for c in asked if not c.is_ad]) == MIN_ORGANIC_ASKED
        assert len(asked) == 4

    def test_options_stay_sorted_by_score(self):
        asked = pick_options(self._pool(ads=6, organic=2), ask_n=4)

        assert [c.score for c in asked] == sorted((c.score for c in asked), reverse=True)

    def test_nothing_changes_when_non_ads_already_lead(self):
        pool = self._pool(ads=2, organic=6)
        pool = sorted(pool, key=lambda c: c.is_ad)  # 非广告排前面

        assert pick_options(pool, ask_n=4) == pool[:4]

    def test_an_all_ad_pool_is_left_alone(self):
        # 没有非广告可换，不能因此少给选项
        pool = self._pool(ads=6, organic=0)

        assert len(pick_options(pool, ask_n=4)) == 4

    def test_a_single_non_ad_is_still_promoted(self):
        asked = pick_options(self._pool(ads=6, organic=1), ask_n=4)

        assert len([c for c in asked if not c.is_ad]) == 1

    def test_fewer_candidates_than_asked_for(self):
        assert len(pick_options(self._pool(ads=1, organic=1), ask_n=4)) == 2

    def test_the_returned_objects_are_the_originals(self):
        # 恢复时要靠 id() 把选项序号映回 top 的下标，不能是拷贝
        pool = self._pool(ads=6, organic=2)

        assert all(any(c is p for p in pool) for c in pick_options(pool, ask_n=4))
