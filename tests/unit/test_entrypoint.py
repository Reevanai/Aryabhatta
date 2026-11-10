import httpx
import pytest
from sentinel_api import entrypoint
from sentinel_core.config import get_settings

REQUIRED = ["SENTINEL_DATABASE_URL_APP", "SENTINEL_DATABASE_URL_VAULT"]


@pytest.fixture
def no_config(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*REQUIRED, "SENTINEL_LOCAL_MASTER_KEY", "SENTINEL_KEY_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()


async def _client(app: object) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")  # type: ignore[arg-type]


async def test_without_configuration_it_serves_docs_and_refuses_api_calls(no_config: None) -> None:
    app = entrypoint.build()
    async with await _client(app) as http:
        health = await http.get("/healthz")
        assert health.json() == {"status": "not_configured", "missing": REQUIRED}
        assert (await http.get("/")).json()["documentation"] == "/v1/docs"
        assert (await http.get("/v1/docs")).status_code == 200
        paths = (await http.get("/v1/openapi.json")).json()["paths"]
        assert "/v1/ingestion/batches" in paths
        for method, path in (
            ("get", "/v1/tenant"),
            ("post", "/v1/pii/reveal"),
            ("get", "/v1/audit/events"),
        ):
            response = await http.request(
                method, path, headers={"Authorization": "Bearer anything"}
            )
            assert response.status_code == 503
            assert response.headers["content-type"].startswith("application/problem+json")
            assert "SENTINEL_DATABASE_URL_APP" in response.json()["detail"]


async def test_missing_key_service_configuration_is_reported(
    no_config: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in REQUIRED:
        monkeypatch.setenv(name, "postgresql+asyncpg://u:p@127.0.0.1:1/none")
    get_settings.cache_clear()
    app = entrypoint.build()
    async with await _client(app) as http:
        assert "SENTINEL_LOCAL_MASTER_KEY" in (await http.get("/healthz")).json()["missing"][0]
    get_settings.cache_clear()


def test_top_level_module_exposes_the_app() -> None:
    import app as top_level

    assert top_level.app is entrypoint.app
