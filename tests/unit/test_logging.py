import json
from typing import Any

import pytest
import structlog
from sentinel_core.logging import REDACTED, configure_logging, scrub_processor, scrub_text


@pytest.mark.parametrize(
    "leak",
    [
        "ABCPT1234Z",
        "person1@example.invalid",
        "9000000001",
        "+91 9000000001",
        "1234 5678 9012",
        "001122330000017",
    ],
)
def test_identifier_patterns_are_removed_from_free_text(leak: str) -> None:
    scrubbed = scrub_text(f"failed for {leak} today")
    assert leak not in scrubbed
    assert REDACTED in scrubbed


def test_tokens_and_ordinary_text_survive() -> None:
    token = "tok_pan_" + "A" * 32
    assert scrub_text(token) == token
    assert scrub_text("batch completed with 12 errors") == "batch completed with 12 errors"


def test_sensitive_keys_are_redacted_whatever_the_value() -> None:
    event: dict[str, Any] = {
        "event": "x",
        "full_name": "Test Person",
        "nested": {"email": "a@b.co", "count": 3},
        "account_number": 42,
        "name_token": "tok_name_" + "B" * 32,
        "tenant_id": "0192f000-0000-7000-8000-000000000001",
    }
    out = scrub_processor(None, "info", event)
    assert out["full_name"] == REDACTED
    assert out["nested"] == {"email": REDACTED, "count": 3}
    assert out["account_number"] == REDACTED
    assert out["name_token"].startswith("tok_name_")
    assert out["tenant_id"] == "0192f000-0000-7000-8000-000000000001"


def test_configured_logger_emits_json_without_personal_data(
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_logging("INFO")
    structlog.get_logger("t").info("row_rejected", pan="ABCPT1234Z", note="call 9000000001")
    line = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert line["event"] == "row_rejected"
    assert "ABCPT1234Z" not in json.dumps(line)
    assert "9000000001" not in json.dumps(line)
