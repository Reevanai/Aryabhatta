"""The implemented API matches the published contract for the phases built so far."""

from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from openapi_spec_validator import validate
from sentinel_api.platform_routes import router as platform_router
from sentinel_ingestion.public import router as ingestion_router

BUILT_PHASE = 2
SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "api" / "openapi.yaml"
METHODS = {"get", "post", "put", "patch", "delete"}


def _spec() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    return loaded


def _implemented() -> dict[tuple[str, str], dict[str, Any]]:
    app = FastAPI()
    app.include_router(platform_router)
    app.include_router(ingestion_router)
    return {
        (path, method): operation
        for path, item in app.openapi()["paths"].items()
        for method, operation in item.items()
        if method in METHODS
    }


def _designed(spec: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {
        (path, method): operation
        for path, item in spec["paths"].items()
        for method, operation in item.items()
        if method in METHODS and operation["x-sentinel-phase"] <= BUILT_PHASE
    }


def test_contract_is_valid_openapi() -> None:
    validate(_spec())


def test_every_operation_due_by_this_phase_is_implemented() -> None:
    assert set(_designed(_spec())) - set(_implemented()) == set()


def test_nothing_is_implemented_that_the_contract_does_not_describe() -> None:
    spec = _spec()
    described = {
        (path, method)
        for path, item in spec["paths"].items()
        for method in item
        if method in METHODS
    }
    assert set(_implemented()) - described == set()


def test_success_status_codes_match() -> None:
    implemented = _implemented()
    for key, operation in _designed(_spec()).items():
        wanted = {code for code in operation["responses"] if code.startswith("2")}
        actual = {code for code in implemented[key]["responses"] if code.startswith("2")}
        assert wanted == actual, key


def test_query_and_path_parameters_match() -> None:
    spec = _spec()
    implemented = _implemented()

    def names(operation: dict[str, Any]) -> set[str]:
        found = set()
        for parameter in operation.get("parameters", []):
            if "$ref" in parameter:
                parameter = spec["components"]["parameters"][parameter["$ref"].rsplit("/", 1)[1]]
            if parameter["in"] in {"query", "path"}:
                found.add(parameter["name"])
        return found

    for key, operation in _designed(spec).items():
        actual = {
            p["name"]
            for p in implemented[key].get("parameters", [])
            if p["in"] in {"query", "path"}
        }
        assert names(operation) == actual, key
