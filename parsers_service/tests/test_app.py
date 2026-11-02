import pytest
from fastapi.testclient import TestClient

from main import app
from universal.page_parser import clear_page_cache

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_page_cache()
    yield
    clear_page_cache()


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


def test_parse_page_chunks_and_cache(mocker):
    long_text = "[URL: https://long.example/doc]\n\n" + "\n\n".join(
        f"Абзац номер {i}: " + ("слово " * 180) for i in range(30)
    )
    mock_parser = mocker.patch(
        "main.browser_parser",
        new=mocker.AsyncMock(return_value=long_text),
    )

    r0 = client.post("/parse/page", json={"urls": ["https://long.example/doc"]})
    assert r0.status_code == 200
    text0 = r0.json()["text"]
    assert "Фрагмент 0 из 3" in text0
    assert "chunk_indices=[1]" in text0
    assert "Абзац номер 0:" in text0
    assert mock_parser.call_count == 1

    # Второй запрос за фрагментом 1 и 2 должен прийти из кэша без повторного вызова browser_parser
    r1 = client.post(
        "/parse/page",
        json={"urls": ["https://long.example/doc"], "chunk_indices": [1, 2]},
    )
    assert r1.status_code == 200
    text1 = r1.json()["text"]
    assert "Фрагмент 1 из 3" in text1
    assert "Фрагмент 2 из 3" in text1
    assert "Абзац номер 29:" in text1
    assert mock_parser.call_count == 1


def test_parse_page_invalid_payload():
    response = client.post("/parse/page", json={"urls": "not-a-list"})
    assert response.status_code == 422
