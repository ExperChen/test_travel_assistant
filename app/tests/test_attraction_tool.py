from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.tools.attraction_tool as attraction_tool


def test_get_attraction_info_without_api_key_returns_safe_defaults(monkeypatch, tmp_path):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.setattr(attraction_tool, "_CACHE_PATH", tmp_path / "attraction_cache.json")
    monkeypatch.setattr(
        attraction_tool,
        "fetch_wikipedia_summary",
        lambda attraction_name, location=None: {"description": "", "image_url": "", "source_url": ""},
    )
    monkeypatch.setattr(
        attraction_tool,
        "fetch_nominatim_place",
        lambda attraction_name, location=None: {"osm_url": ""},
    )

    result = attraction_tool.get_attraction_info("Tokyo Tower", "Tokyo")

    assert result["name"] == "Tokyo Tower"
    assert result["image_url"] == ""
    assert result["ticket_price"] == ""
    assert "estimated" in result["visit_duration"]
    assert isinstance(result["sources"], list)


def test_get_attraction_info_extracts_core_fields(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "fake-key")

    def fake_google_search(query: str, api_key: str, num: int = 10):
        return {
            "knowledge_graph": {
                "hours": "Open: 9:00 AM - 6:00 PM",
                "description": "Ticket price RM 80 for adults",
            },
            "answer_box": {"answer": "Recommended time: 2 hours"},
            "organic_results": [
                {
                    "title": "Official Site",
                    "link": "https://example.com/official",
                    "snippet": "Opening hours 9:00 AM - 6:00 PM",
                    "thumbnail": "https://img.example.com/thumb.jpg",
                },
                {
                    "title": "Travel Guide",
                    "link": "https://example.com/guide",
                    "snippet": "Admission fee RM 80",
                },
                {
                    "title": "Visit Tips",
                    "link": "https://example.com/tips",
                    "snippet": "How long to spend: 2 hours",
                },
            ],
        }

    def fake_google_images(query: str, api_key: str, num: int = 10):
        return {
            "images_results": [
                {
                    "title": "Spot Image",
                    "original": "https://img.example.com/original.jpg",
                    "thumbnail": "https://img.example.com/tn.jpg",
                    "link": "https://example.com/image",
                }
            ]
        }

    monkeypatch.setattr(attraction_tool, "_search_google", fake_google_search)
    monkeypatch.setattr(attraction_tool, "_search_google_images", fake_google_images)
    monkeypatch.setattr(attraction_tool, "_CACHE_PATH", attraction_tool.Path("/tmp/attraction_tool_test_cache.json"))

    result = attraction_tool.get_attraction_info("Demo Attraction", "Kuala Lumpur")

    assert result["image_url"].startswith("http")
    assert result["opening_hours"] != ""
    assert result["visit_duration"] != ""
    assert "RM" in result["ticket_price"]
    assert len(result["sources"]) >= 3
