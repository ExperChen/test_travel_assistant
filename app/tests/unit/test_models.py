"""数据契约测试：校验规则、坐标系纪律、SerpAPI 参数映射。"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.models.common import CityRef, GeoPoint, LocaleCtx, QuotaCounter
from app.models.errors import ApiError, ErrorCode
from app.models.events import InterruptQuestion, QuestionOption, TripEvent
from app.models.flight import (
    Airport,
    AirportTime,
    FlightItinerary,
    FlightLeg,
    FlightSearchParams,
)
from app.models.hotel import HotelSearchParams
from app.models.trip import TripRequest


class TestGeoPoint:
    def test_amap_string_is_parsed_as_gcj02(self):
        p = GeoPoint.from_amap("116.397428,39.909230")
        assert (p.lng, p.lat) == (116.397428, 39.909230)
        assert p.crs == "GCJ02"

    def test_google_gps_is_parsed_as_wgs84(self):
        p = GeoPoint.from_google({"latitude": 39.915, "longitude": 116.404})
        assert p is not None
        assert p.crs == "WGS84"

    def test_google_gps_missing_returns_none(self):
        assert GeoPoint.from_google(None) is None
        assert GeoPoint.from_google({}) is None
        assert GeoPoint.from_google({"latitude": None, "longitude": 1.0}) is None

    def test_to_amap_converts_wgs84_before_formatting(self):
        """最关键的一条纪律：Google 坐标进高德前必须转换，否则静默偏几百米。"""
        google = GeoPoint.wgs84(116.404, 39.915)
        amap_str = google.to_amap()
        assert amap_str != "116.404000,39.915000"
        lng, lat = (float(x) for x in amap_str.split(","))
        assert lng > 116.404 and lat > 39.915

    def test_to_amap_leaves_gcj02_untouched(self):
        p = GeoPoint.gcj02(116.41024, 39.91640)
        assert p.to_amap() == "116.410240,39.916400"

    def test_conversions_are_idempotent(self):
        p = GeoPoint.gcj02(116.41024, 39.9164)
        assert p.as_gcj02() is p
        assert p.as_wgs84().as_gcj02().lng == pytest.approx(p.lng, abs=1e-9)

    def test_distance_normalises_both_sides(self):
        gcj = GeoPoint.gcj02(116.41024, 39.91640)
        same_place_wgs = GeoPoint.wgs84(116.404, 39.915)
        # 同一个物理位置的两种表示，距离应接近 0；不做转换会得到 ~500m
        assert gcj.distance_to(same_place_wgs) < 30

    def test_is_frozen(self):
        p = GeoPoint.gcj02(116.4, 39.9)
        with pytest.raises(ValidationError):
            p.lng = 1.0  # type: ignore[misc]

    def test_rejects_out_of_range(self):
        with pytest.raises(ValidationError):
            GeoPoint(lng=200, lat=39.9)


class TestCityRef:
    def _city(self, adcode: str) -> CityRef:
        return CityRef(name="测试市", adcode=adcode, center=GeoPoint.gcj02(116.4, 39.9))

    @pytest.mark.parametrize("adcode", ["110000", "310000", "330100", "440300"])
    def test_mainland_cities(self, adcode):
        assert self._city(adcode).is_mainland_china

    @pytest.mark.parametrize("adcode", ["810000", "820000", "710000"])
    def test_hk_macau_taiwan_are_not_covered(self, adcode):
        # 高德 Web 服务不覆盖港澳台，必须在 intake 就拦下来（架构文档 §1.4）
        assert not self._city(adcode).is_mainland_china

    def test_missing_adcode_is_not_assumed_mainland(self):
        assert not self._city("").is_mainland_china


class TestTripRequest:
    def _req(self, **kw) -> TripRequest:
        base = {
            "departure_city": "北京",
            "destination_city": "杭州",
            "outbound_date": date(2026, 8, 10),
            "return_date": date(2026, 8, 16),
        }
        return TripRequest(**{**base, **kw})

    def test_minimal_request(self):
        req = self._req()
        assert req.adults == 1
        assert req.pace == "standard"
        assert req.transport == "transit"
        assert not req.auto_select

    def test_nights_and_travel_days(self):
        req = self._req()
        assert req.nights == 6
        assert req.travel_days == 7

    def test_return_must_be_after_outbound(self):
        with pytest.raises(ValidationError):
            self._req(return_date=date(2026, 8, 10))
        with pytest.raises(ValidationError):
            self._req(return_date=date(2026, 8, 1))

    def test_children_ages_length_must_match(self):
        with pytest.raises(ValidationError):
            self._req(children=2, children_ages=[5])
        assert self._req(children=2, children_ages=[5, 8]).children == 2

    def test_child_age_range(self):
        with pytest.raises(ValidationError):
            self._req(children=1, children_ages=[0])
        with pytest.raises(ValidationError):
            self._req(children=1, children_ages=[18])

    def test_city_names_are_stripped(self):
        assert self._req(destination_city="  杭州  ").destination_city == "杭州"

    def test_blank_city_rejected(self):
        with pytest.raises(ValidationError):
            self._req(destination_city="   ")


class TestFlightSearchParams:
    def _full(self, **kw) -> FlightSearchParams:
        base = dict(
            departure_airport_id="pek",
            arrival_airport_id="HGH",
            departure_date=date(2026, 8, 10),
            return_date=date(2026, 8, 16),
            is_round_trip=True,
            passengers=1,
            travel_class="economy",
        )
        return FlightSearchParams(**{**base, **kw})

    def test_iata_is_uppercased(self):
        assert self._full().departure_airport_id == "PEK"

    def test_is_ready_requires_every_mandatory_field(self):
        assert self._full().is_ready
        assert not FlightSearchParams().is_ready
        assert not self._full(arrival_airport_id=None).is_ready
        assert not self._full(is_round_trip=None).is_ready

    def test_round_trip_requires_return_date(self):
        assert not self._full(return_date=None).is_ready

    def test_return_date_must_not_precede_departure(self):
        assert not self._full(return_date=date(2026, 8, 1)).is_ready

    def test_one_way_does_not_need_return_date(self):
        assert self._full(is_round_trip=False, return_date=None).is_ready

    def test_to_serpapi_shape(self):
        params = self._full().to_serpapi(currency="CNY", hl="zh-CN")
        assert params == {
            "engine": "google_flights",
            "departure_id": "PEK",
            "arrival_id": "HGH",
            "outbound_date": "2026-08-10",
            "return_date": "2026-08-16",
            "type": 1,  # 1=往返（文档写反了，已实测确认）
            "adults": 1,
            "currency": "CNY",
            "hl": "zh-CN",
            "travel_class": 1,
        }

    def test_one_way_uses_type_2_and_omits_return(self):
        params = self._full(is_round_trip=False, return_date=None).to_serpapi()
        assert params["type"] == 2
        assert "return_date" not in params

    def test_incomplete_params_refuse_to_build_a_request(self):
        # 宁可在这里炸，也不要发一个必然返回空结果的请求白烧额度
        with pytest.raises(ValueError):
            FlightSearchParams().to_serpapi()


class TestFlightItinerary:
    def _itinerary(self) -> FlightItinerary:
        return FlightItinerary(
            flights=[
                FlightLeg(
                    departure_airport=AirportTime(id="PEK", time="2026-08-10 08:30"),
                    arrival_airport=AirportTime(id="SHA", time="2026-08-10 11:00"),
                ),
                FlightLeg(
                    departure_airport=AirportTime(id="SHA", time="2026-08-10 13:00"),
                    arrival_airport=AirportTime(id="HGH", time="2026-08-10 14:15"),
                ),
            ],
            layovers=[{"duration": 120, "name": "虹桥", "id": "SHA"}],  # type: ignore[list-item]
            total_duration=345,
        )

    def test_arrival_time_comes_from_the_last_leg(self):
        # route_planner 的首日时间窗全靠这个值
        assert self._itinerary().arrives_at == datetime(2026, 8, 10, 14, 15)

    def test_departure_time_comes_from_the_first_leg(self):
        assert self._itinerary().departs_at == datetime(2026, 8, 10, 8, 30)

    def test_arrival_airport_id(self):
        assert self._itinerary().arrival_airport_id == "HGH"

    def test_stops_counts_layovers(self):
        assert self._itinerary().stops == 1

    def test_unparsable_time_returns_none_instead_of_raising(self):
        leg = FlightLeg(
            departure_airport=AirportTime(id="PEK", time="不是时间"),
            arrival_airport=AirportTime(id="HGH", time=""),
        )
        assert FlightItinerary(flights=[leg]).arrives_at is None

    def test_empty_itinerary_is_safe(self):
        empty = FlightItinerary()
        assert empty.arrives_at is None
        assert empty.arrival_airport_id == ""
        assert empty.stops == 0


class TestAirport:
    def test_label_format(self):
        a = Airport(name="首都国际机场", id="pek", city="北京", distance="25 km")
        assert a.id == "PEK"
        assert a.label == "[PEK] 首都国际机场 - 距市中心 25 km"

    def test_label_without_distance(self):
        assert Airport(name="萧山机场", id="HGH").label == "[HGH] 萧山机场"


class TestHotelSearchParams:
    def _params(self, **kw) -> HotelSearchParams:
        base = dict(
            q="杭州 西湖 酒店",
            check_in_date=date(2026, 8, 10),
            check_out_date=date(2026, 8, 16),
        )
        return HotelSearchParams(**{**base, **kw})

    def test_checkout_must_be_after_checkin(self):
        with pytest.raises(ValidationError):
            self._params(check_out_date=date(2026, 8, 10))

    def test_children_ages_must_match_count(self):
        with pytest.raises(ValidationError):
            self._params(children=2, children_ages=[5])

    def test_needs_q_or_property_token(self):
        with pytest.raises(ValidationError):
            self._params(q="")
        assert self._params(q="", property_token="tok").property_token == "tok"

    def test_to_serpapi_shape(self):
        params = self._params(hotel_class=[4, 5], max_price=800).to_serpapi(
            gl="cn", hl="zh-CN", currency="CNY"
        )
        assert params["engine"] == "google_hotels"
        assert params["check_in_date"] == "2026-08-10"
        assert params["hotel_class"] == "4,5"
        assert params["max_price"] == 800
        assert params["currency"] == "CNY"

    def test_children_ages_are_comma_joined(self):
        params = self._params(children=2, children_ages=[5, 8]).to_serpapi(
            gl="cn", hl="zh-CN", currency="CNY"
        )
        assert params["children"] == 2
        assert params["children_ages"] == "5,8"

    def test_vacation_rentals_drops_hotel_only_filters(self):
        # hotels 专属参数在民宿模式下会被服务端静默忽略，不如根本不发
        params = self._params(
            vacation_rentals=True, hotel_class=[4, 5], free_cancellation=True
        ).to_serpapi(gl="cn", hl="zh-CN", currency="CNY")
        assert params["vacation_rentals"] == "true"
        assert "hotel_class" not in params
        assert "free_cancellation" not in params

    def test_unset_optional_filters_are_omitted(self):
        params = self._params().to_serpapi(gl="cn", hl="zh-CN", currency="CNY")
        for key in ("sort_by", "min_price", "max_price", "rating", "next_page_token"):
            assert key not in params


class TestInterruptQuestion:
    def _options(self):
        return [
            QuestionOption(key="PVG", label="[PVG] 浦东国际机场"),
            QuestionOption(key="SHA", label="[SHA] 虹桥国际机场"),
        ]

    def test_default_falls_back_to_first_option(self):
        q = InterruptQuestion.build("flight.arrival_airport", "选哪个机场？", self._options())
        assert q.default == "PVG"

    def test_explicit_default(self):
        q = InterruptQuestion.build(
            "flight.arrival_airport", "选哪个机场？", self._options(), default="SHA"
        )
        assert q.default == "SHA"

    def test_rejects_empty_options(self):
        # 没有选项就没有默认值，超时后行程会永久卡死
        with pytest.raises(ValueError):
            InterruptQuestion.build("x", "?", [])

    def test_accepts_only_listed_keys(self):
        q = InterruptQuestion.build("x", "?", self._options())
        assert q.accepts("SHA")
        assert not q.accepts("PEK")

    def test_expiry(self):
        q = InterruptQuestion.build("x", "?", self._options(), timeout_s=600)
        assert not q.is_expired
        expired = q.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
        )
        assert expired.is_expired


class TestEventsAndErrors:
    def test_event_helpers_carry_seq_and_type(self):
        evt = TripEvent.stage(3, "flight", "正在搜索往返航班…")
        assert evt.seq == 3
        assert evt.type == "stage"
        assert evt.data == {"phase": "flight", "label": "正在搜索往返航班…"}

    def test_question_event_is_json_serialisable(self):
        q = InterruptQuestion.build("x", "?", [QuestionOption(key="a", label="A")])
        evt = TripEvent.question(1, q)
        assert isinstance(evt.data["expires_at"], str)

    def test_api_error_fills_user_message_and_retriable(self):
        err = ApiError.of(ErrorCode.UPSTREAM_TIMEOUT, "httpx read timeout on google_flights")
        assert err.retriable
        assert "稍后" in err.user_message or "重试" in err.user_message
        assert "httpx" not in err.user_message  # 技术细节不能漏给用户

    def test_non_retriable_errors(self):
        assert not ApiError.of(ErrorCode.INVALID_PARAMS, "bad date").retriable
        assert not ApiError.of(ErrorCode.DESTINATION_UNSUPPORTED, "tokyo").retriable

    def test_details_are_preserved(self):
        err = ApiError.of(ErrorCode.NO_FLIGHTS, "empty", departure="PEK", arrival="HGH")
        assert err.details == {"departure": "PEK", "arrival": "HGH"}


class TestMisc:
    def test_locale_defaults_to_china(self):
        loc = LocaleCtx()
        assert (loc.gl, loc.hl, loc.currency) == ("cn", "zh-CN", "CNY")

    def test_quota_counter(self):
        q = QuotaCounter()
        q.bump("serpapi")
        q.bump("serpapi")
        q.bump("amap")
        q.bump("serpapi", cached=True)
        assert q.serpapi == 2
        assert q.amap == 1
        assert q.cache_hits == 1
