"""EDITOR_BASE_URL: один URL, JSON-карта или main=..,sigma=..; выбор ссылки по gateway_target."""
import pytest

from agent.core import config


@pytest.mark.parametrize("raw, expected", [
    ("https://a.example", {"main": "https://a.example"}),
    ('{"main":"https://a.example","Sigma":"https://b.example/"}',
     {"main": "https://a.example", "sigma": "https://b.example/"}),
    ("main=https://a.example, SIGMA = https://b.example", {"main": "https://a.example", "sigma": "https://b.example"}),
    ("main=https://a.example?x=1&y=2", {"main": "https://a.example?x=1&y=2"}),
    ("main=https://a.example,sigma=", {"main": "https://a.example"}),
    ('{"main": "", "sigma": "https://b.example"}', {"sigma": "https://b.example"}),
    ("{broken json", {}),
    ("", {}),
    ("   ", {}),
])
def test_parse_editor_base_urls(raw, expected):
    assert config._parse_editor_base_urls(raw) == expected


def test_url_with_port_is_single_url_not_map():
    # "http://host:7000" не должен распознаться как ключ=значение
    assert config._parse_editor_base_urls("http://localhost:7000") == {"main": "http://localhost:7000"}


@pytest.fixture
def urls(monkeypatch):
    def _set(m):
        monkeypatch.setattr(config, "EDITOR_BASE_URLS", m)
    return _set


def test_target_picks_its_domain(urls):
    urls({"main": "https://a.example/", "sigma": "https://b.example/"})
    assert config.editor_base_url("sigma") == "https://b.example"
    assert config.editor_base_url("SIGMA ") == "https://b.example"
    assert config.editor_base_url("main") == "https://a.example"


@pytest.mark.parametrize("target", [None, "", "telegram", "max"])
def test_unknown_target_falls_back_to_main(urls, target):
    urls({"main": "https://a.example", "sigma": "https://b.example"})
    assert config.editor_base_url(target) == "https://a.example"


def test_no_main_falls_back_to_first(urls):
    urls({"sigma": "https://b.example", "other": "https://c.example"})
    assert config.editor_base_url("telegram") == "https://b.example"


def test_single_url_serves_every_target(urls):
    urls(config._parse_editor_base_urls("https://a.example"))
    assert config.editor_base_url("sigma") == config.editor_base_url("main") == "https://a.example"
