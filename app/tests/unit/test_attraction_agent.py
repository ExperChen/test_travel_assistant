"""景点打分与筛选测试（纯函数，不碰网络）。"""

from __future__ import annotations

import pytest

from app.agents.attraction_agent import (
    MAX_SELECTED,
    RANK_HORIZON,
    attractions_centroid,
    looks_like_match,
    recall_with_report,
    score_attractions,
    select_attractions,
    stay_minutes,
    type_weight,
)
from app.models.attraction import Attraction
from app.models.common import CityRef, GeoPoint

CITY_CENTER = GeoPoint.gcj02(120.209947, 30.246026)  # 杭州
CITY = CityRef(name="杭州市", adcode="330100", citycode="0571", center=CITY_CENTER)


def make(
    name: str,
    *,
    poi_id: str | None = None,
    parent_id: str = "",
    typecode: str = "110000",
    rating: float | None = 4.5,
    lng: float = 120.21,
    lat: float = 30.25,
    photos: bool = True,
    opentime: str = "08:00-18:00",
    business_area: str = "",
    must_visit: bool = False,
    entrance: GeoPoint | None = None,
    recall_rank: int | None = 0,
) -> Attraction:
    return Attraction(
        poi_id=poi_id or name,
        parent_id=parent_id,
        name=name,
        location=GeoPoint.gcj02(lng, lat),
        entrance=entrance,
        typecode=typecode,
        rating=rating,
        opentime_today=opentime,
        business_area=business_area,
        photos=["https://x/1.jpg"] if photos else [],
        must_visit=must_visit,
        recall_rank=recall_rank,
    )


class TestTypeWeight:
    def test_exact_match(self):
        assert type_weight("110201") == 1.00  # 世界遗产
        assert type_weight("110000") == 0.95  # 风景名胜

    def test_falls_back_to_major_category(self):
        # 110399 未单独收录，退到 110300（博物馆大类）
        assert type_weight("110399") == type_weight("110300")

    def test_unknown_typecode_gets_neutral_weight(self):
        assert type_weight("050000") == 0.50  # 餐饮，不该被当成景点抬分
        assert type_weight("") == 0.50


class TestStayMinutes:
    @pytest.mark.parametrize(
        "pace,expected", [("relaxed", 150), ("standard", 120), ("packed", 90)]
    )
    def test_pace_drives_base_duration(self, pace, expected):
        assert stay_minutes(make("普通景点"), pace) == expected

    def test_large_scenic_area_takes_longer(self):
        big = make("西湖风景名胜区", business_area="西湖景区")
        assert big.is_large_scenic_area
        assert stay_minutes(big, "standard") == 180  # 120 × 1.5


class TestScoreAttractions:
    def test_higher_rating_scores_higher(self):
        pool = [make("低分", rating=3.0), make("高分", rating=4.9)]
        ranked = score_attractions(pool, CITY_CENTER)
        assert [a.name for a in ranked] == ["高分", "低分"]

    def test_type_weight_breaks_ties(self):
        pool = [
            make("剧院", typecode="110600", rating=4.5),
            make("世界遗产", typecode="110201", rating=4.5),
        ]
        ranked = score_attractions(pool, CITY_CENTER)
        assert ranked[0].name == "世界遗产"

    def test_closer_to_anchor_scores_higher(self):
        pool = [
            make("远", lng=120.60, lat=30.60),
            make("近", lng=120.21, lat=30.25),
        ]
        ranked = score_attractions(pool, CITY_CENTER)
        assert ranked[0].name == "近"

    def test_missing_rating_gets_neutral_not_zero(self):
        # 没被评过分的冷门去处不该直接判死刑
        unrated = score_attractions([make("无评分", rating=None)], CITY_CENTER)[0]
        low_rated = score_attractions([make("低分", rating=1.0)], CITY_CENTER)[0]
        assert unrated.score > low_rated.score

    def test_completeness_bonus(self):
        rich = score_attractions([make("有图有营业时间")], CITY_CENTER)[0]
        bare = score_attractions([make("啥都没有", photos=False, opentime="")], CITY_CENTER)[0]
        assert rich.score > bare.score

    def test_must_visit_always_ranks_first(self):
        pool = [
            make("超高分", rating=5.0, typecode="110201"),
            make("用户指定的冷门店", rating=2.0, typecode="110600", must_visit=True),
        ]
        ranked = score_attractions(pool, CITY_CENTER)
        assert ranked[0].name == "用户指定的冷门店"

    def test_avoid_filters_by_name(self):
        pool = [make("动物园", typecode="110104"), make("西湖")]
        ranked = score_attractions(pool, CITY_CENTER, avoid=["动物园"])
        assert [a.name for a in ranked] == ["西湖"]

    def test_avoid_never_drops_a_must_visit(self):
        # 用户既写了必去又写了排除，以必去为准，不要自作主张删掉
        pool = [make("动物园", typecode="110104", must_visit=True)]
        assert len(score_attractions(pool, CITY_CENTER, avoid=["动物园"])) == 1

    def test_suggested_duration_is_filled_in(self):
        scored = score_attractions([make("景点")], CITY_CENTER, "packed")
        assert scored[0].suggested_duration_min == 90

    def test_empty_pool(self):
        assert score_attractions([], CITY_CENTER) == []


class TestPopularitySignal:
    """高德关键字搜索的名次是这套数据里唯一的知名度信号。

    实测教训：不用它的话，杭州会把"崇一堂""江堤步道"排在西湖前面——
    评分普遍挤在 4.2~4.9 区分不开，而"距市中心"指向的是钱江新城 CBD 而非旅游核心区。
    """

    def test_better_recall_rank_wins(self):
        pool = [
            make("冷门但评分高", rating=4.9, recall_rank=18),
            make("头部景点", rating=4.6, recall_rank=0),
        ]
        ranked = score_attractions(pool, CITY_CENTER)
        assert ranked[0].name == "头部景点"

    def test_popularity_beats_proximity_to_city_centre(self):
        # CBD 旁边的冷门 POI 不该压过城西的头牌景区
        pool = [
            make("CBD 旁的小广场", lng=120.21, lat=30.25, rating=4.7, recall_rank=25),
            make("西湖", lng=120.15, lat=30.27, rating=4.9, recall_rank=0),
        ]
        ranked = score_attractions(pool, CITY_CENTER)
        assert ranked[0].name == "西湖"

    def test_around_only_pois_get_below_median_popularity(self):
        # 只在周边搜索里出现过（无名次）：压一档，但不至于直接出局
        ranked = score_attractions(
            [make("关键字命中", recall_rank=3), make("仅周边命中", recall_rank=None)],
            CITY_CENTER,
        )
        assert ranked[0].name == "关键字命中"
        assert ranked[1].score > 0.4

    def test_rank_beyond_horizon_saturates(self):
        far = score_attractions([make("第80名", recall_rank=80)], CITY_CENTER)[0]
        edge = score_attractions([make("第30名", recall_rank=RANK_HORIZON)], CITY_CENTER)[0]
        assert far.score == pytest.approx(edge.score)


class TestSelectAttractions:
    def _pool(self, n: int) -> list[Attraction]:
        return score_attractions(
            [make(f"景点{i}", poi_id=f"p{i}", rating=5.0 - i * 0.1) for i in range(n)],
            CITY_CENTER,
        )

    def test_limit_is_four_per_day(self):
        assert len(select_attractions(self._pool(30), travel_days=3)) == 12

    def test_hard_cap(self):
        assert len(select_attractions(self._pool(60), travel_days=10)) == MAX_SELECTED

    def test_takes_everything_when_pool_is_small(self):
        assert len(select_attractions(self._pool(5), travel_days=7)) == 5

    def test_must_visit_survives_the_cut(self):
        pool = [make(f"景点{i}", poi_id=f"p{i}", rating=5.0) for i in range(20)]
        pool.append(make("必去的冷门地", poi_id="must", rating=1.0, must_visit=True))
        selected = select_attractions(score_attractions(pool, CITY_CENTER), travel_days=1)

        assert len(selected) == 4
        assert any(a.must_visit for a in selected)

    def test_zero_days_is_treated_as_one(self):
        assert len(select_attractions(self._pool(10), travel_days=0)) == 4

    def test_sub_areas_of_a_selected_scenic_area_are_skipped(self):
        """杭州实测：高德把景区内部分区命名成「父名-子名」。

        不过滤的话一个西湖吃掉 3 个名额，而用户只是想去西湖玩一天。
        """
        pool = score_attractions(
            [
                make("杭州西湖风景名胜区", poi_id="xihu", rating=4.9, recall_rank=0),
                make(
                    "杭州西湖风景名胜区-断桥残雪",
                    poi_id="duanqiao",
                    parent_id="xihu",
                    rating=4.9,
                    recall_rank=1,
                ),
                make(
                    "杭州西湖风景名胜区-柳浪闻莺",
                    poi_id="liulang",
                    parent_id="xihu",
                    rating=4.8,
                    recall_rank=2,
                ),
                make("灵隐寺", poi_id="lingyin", rating=4.8, recall_rank=3),
            ],
            CITY_CENTER,
        )
        selected = select_attractions(pool, travel_days=1)

        assert [a.poi_id for a in selected] == ["xihu", "lingyin"]

    def test_independent_attractions_inside_a_container_are_kept(self):
        """深圳实测：「华侨城旅游度假区」是个容器，世界之窗才是目的地。

        只看 parent 字段会把第 2 高分的世界之窗挤掉，而容器本身又没排进行程——
        用户两个都拿不到。判别信号是名字有没有父名前缀。
        """
        pool = score_attractions(
            [
                make("深圳华侨城旅游度假区", poi_id="oct", rating=4.7, recall_rank=3),
                make("深圳世界之窗", poi_id="wow", parent_id="oct", rating=4.8, recall_rank=4),
                make(
                    "锦绣中华民俗村", poi_id="splendid", parent_id="oct", rating=4.6, recall_rank=8
                ),
            ],
            CITY_CENTER,
        )
        selected = select_attractions(pool, travel_days=1)

        assert {a.poi_id for a in selected} == {"oct", "wow", "splendid"}

    def test_sub_area_survives_when_its_parent_is_not_selected(self):
        # 父景区没进最终名单时，分区自己有资格入选
        pool = score_attractions(
            [make("某景区-某分区", poi_id="child", parent_id="absent-parent", recall_rank=0)],
            CITY_CENTER,
        )
        assert len(select_attractions(pool, travel_days=1)) == 1

    def test_must_visit_sub_area_is_never_dropped(self):
        pool = score_attractions(
            [
                make("西湖", poi_id="xihu", rating=4.9, recall_rank=0),
                make(
                    "西湖-断桥残雪",
                    poi_id="duanqiao",
                    parent_id="xihu",
                    rating=4.0,
                    recall_rank=9,
                    must_visit=True,
                ),
            ],
            CITY_CENTER,
        )
        selected = select_attractions(pool, travel_days=1)
        assert {a.poi_id for a in selected} == {"xihu", "duanqiao"}


class TestCentroid:
    def test_none_for_empty(self):
        assert attractions_centroid([]) is None

    def test_average_of_routing_points(self):
        pts = [make("a", lng=120.0, lat=30.0), make("b", lng=120.2, lat=30.2)]
        c = attractions_centroid(pts)
        assert c.lng == pytest.approx(120.1)
        assert c.lat == pytest.approx(30.1)

    def test_uses_entrance_when_present(self):
        # 大景区的中心点在湖里，入口在岸上——重心必须按入口算
        far_centre = make("大景区", lng=120.5, lat=30.5, entrance=GeoPoint.gcj02(120.0, 30.0))
        assert attractions_centroid([far_centre]).lng == pytest.approx(120.0)


class TestCentroidIgnoresDayTrips:
    """算术平均对离群点毫无抵抗力。

    实测成都：20 个景点里 7 个在 40 km 外（都江堰 64 km、天台山 91 km），
    把重心从市中心拽出 **19.3 km**，于是每家市区酒店测出来都是
    "距景点集中区 40~60 分钟"，通勤这一维在重排里彻底失效。
    剔除远郊后只剩 5.1 km。
    """

    # 市中心附近三个 + 一个 ~90 km 外的远郊（经度差 1° ≈ 96 km）
    NEAR = [
        make("近1", poi_id="n1", lng=120.00, lat=30.00),
        make("近2", poi_id="n2", lng=120.02, lat=30.02),
        make("近3", poi_id="n3", lng=120.04, lat=30.00),
    ]
    FAR = make("远郊景区", poi_id="f1", lng=121.00, lat=30.00)
    CENTRE = GeoPoint.gcj02(120.02, 30.01)

    def test_a_day_trip_does_not_drag_the_anchor(self):
        with_far = attractions_centroid([*self.NEAR, self.FAR], self.CENTRE)
        without_far = attractions_centroid(self.NEAR, self.CENTRE)

        assert with_far.lng == pytest.approx(without_far.lng)

    def test_no_city_centre_means_no_trimming(self):
        # 不传市中心就是老行为：全量平均。调用方没给参照系时不能自己编一个
        plain = attractions_centroid([*self.NEAR, self.FAR])

        assert plain.lng > 120.2

    def test_a_must_visit_is_trimmed_too(self):
        # 用户说要去都江堰，不等于愿意为它把酒店挪到 60 km 外
        far_must = make("远郊必去", poi_id="f2", lng=121.0, lat=30.0, must_visit=True)

        anchored = attractions_centroid([*self.NEAR, far_must], self.CENTRE)

        assert anchored.lng == pytest.approx(attractions_centroid(self.NEAR, self.CENTRE).lng)

    def test_everything_far_falls_back_to_the_full_set(self):
        # 景点都在郊县的小城市：没有锚点比锚点不准更糟
        far_only = [self.FAR, make("远2", poi_id="f3", lng=121.02, lat=30.0)]

        assert attractions_centroid(far_only, self.CENTRE) is not None

    def test_still_none_for_empty(self):
        assert attractions_centroid([], self.CENTRE) is None


class TestMustVisitNotFound:
    """用户点名要去的地方**悄悄消失**是最糟的失败方式。

    他会拿到一份看起来正常的行程，直到出发前才发现没安排。
    """

    @staticmethod
    def _stub(monkeypatch, *, found: set[str]):
        """按关键词返回结果；不在 `found` 里的名字一律搜不到。"""
        import app.agents.attraction_agent as module

        async def fake_poi_keyword(keywords: str = "", **kw):
            if not keywords:  # types-only 的召回分页
                return [make("西湖", recall_rank=0), make("灵隐寺", recall_rank=1),
                        make("西溪湿地", recall_rank=2), make("雷峰塔", recall_rank=3)]
            return [make(keywords)] if keywords in found else []

        monkeypatch.setattr(module, "poi_keyword", fake_poi_keyword)

    async def test_a_missing_spot_produces_a_warning(self, monkeypatch):
        self._stub(monkeypatch, found={"西湖"})

        _, warnings = await recall_with_report(
            CITY, must_visit=["西湖", "不存在的地方"], pages=1
        )

        assert [w.code for w in warnings] == ["MUST_VISIT_NOT_FOUND"]
        assert "不存在的地方" in warnings[0].message
        assert "西湖" not in warnings[0].message.split("；")[0]  # 找到的不该被点名

    async def test_the_warning_suggests_alternatives(self, monkeypatch):
        self._stub(monkeypatch, found=set())

        _, warnings = await recall_with_report(CITY, must_visit=["某个不存在的"], pages=1)

        # 光说"没找到"没用，给几个同城热门景点当台阶
        assert "西湖" in warnings[0].message

    async def test_everything_found_says_nothing(self, monkeypatch):
        self._stub(monkeypatch, found={"西湖", "灵隐寺"})

        pool, warnings = await recall_with_report(
            CITY, must_visit=["西湖", "灵隐寺"], pages=1
        )

        assert warnings == []
        assert any(a.must_visit for a in pool)

    async def test_a_missing_spot_does_not_kill_the_others(self, monkeypatch):
        self._stub(monkeypatch, found={"西湖"})

        pool, _ = await recall_with_report(
            CITY, must_visit=["不存在的地方", "西湖"], pages=1
        )

        assert {a.name for a in pool if a.must_visit} == {"西湖"}

    async def test_no_must_visit_at_all_is_silent(self, monkeypatch):
        self._stub(monkeypatch, found=set())

        _, warnings = await recall_with_report(CITY, pages=1)

        assert warnings == []


class TestLooksLikeMatch:
    """高德对**任何**关键词都会模糊返回一条，所以"搜到了"不等于"命中了"。

    实测：「不存在的地方xyz」→「四川省成都市新津区兴义镇」，
    「zzzqqq不可能存在」→「不可方物」。没有这道校验，
    `MUST_VISIT_NOT_FOUND` 就是一段永远不会执行的死代码。
    """

    @pytest.mark.parametrize(
        ("query", "poi"),
        [
            ("都江堰", "都江堰景区"),
            ("西湖", "西湖风景名胜区"),
            ("宽窄巷子", "宽窄巷子景区"),
            # 官方名常插字，子串匹配会漏掉这种
            ("大熊猫基地", "成都大熊猫繁育研究基地"),
            ("兵马俑", "秦始皇帝陵博物院(兵马俑)"),
        ],
    )
    def test_real_aliases_are_accepted(self, query, poi):
        assert looks_like_match(query, poi)

    @pytest.mark.parametrize(
        ("query", "poi"),
        [
            ("不存在的地方xyz", "四川省成都市新津区兴义镇"),
            ("zzzqqq不可能存在", "不可方物"),
            ("回民街", "成都欢乐谷"),
        ],
    )
    def test_fuzzy_garbage_is_rejected(self, query, poi):
        assert not looks_like_match(query, poi)

    def test_an_empty_query_matches_nothing(self):
        assert not looks_like_match("", "西湖")
        assert not looks_like_match("   ", "西湖")
