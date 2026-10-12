import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"service": "parsers_service", "status": "ok"}


def test_parse_page_success(mocker):
    mock_browser_text = "Текст со страницы"
    mock_parser = mocker.patch(
        "main.browser_parser",
        new=mocker.AsyncMock(return_value=mock_browser_text),
    )

    payload = {
        "urls": ["https://example.com", "https://test.ru"],
        "headers": {"User-Agent": "TestBot"},
    }
    response = client.post("/parse/page", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["urls"] == payload["urls"]
    assert "Текст со страницы" in data["text"]
    assert mock_parser.call_count == 2


def test_parse_page_invalid_payload():
    response = client.post("/parse/page", json={"urls": "not-a-list"})
    assert response.status_code == 422
