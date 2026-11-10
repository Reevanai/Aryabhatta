import pytest
from sentinel_core.errors import Invalid
from sentinel_core.tokenisation import (
    FIELD_TYPES,
    TOKEN_PATTERN,
    compute_token,
    normalise,
    token_field_type,
)

KEY_A = b"a" * 32
KEY_B = b"b" * 32


@pytest.mark.parametrize(
    ("field_type", "left", "right"),
    [
        ("name", "  Test   Person ", "test person"),
        ("addr", "12  Sample St", "12 sample st"),
        ("email", "Person@Example.Invalid", "person@example.invalid"),
        ("vpa", "Shop@Bank", "shop@bank"),
        ("pan", "abcpt1234z", "ABCPT1234Z"),
        ("phone", "+91 90000-00001", "9000000001"),
        ("phone", "919000000001", "9000000001"),
        ("acct", "0011 2233 0001", "001122330001"),
    ],
)
def test_equivalent_spellings_share_a_token(field_type: str, left: str, right: str) -> None:
    assert normalise(field_type, left) == normalise(field_type, right)
    assert compute_token(KEY_A, field_type, left) == compute_token(KEY_A, field_type, right)


def test_tokens_differ_across_tenant_keys() -> None:
    assert compute_token(KEY_A, "pan", "ABCPT1234Z") != compute_token(KEY_B, "pan", "ABCPT1234Z")


def test_tokens_differ_across_field_types() -> None:
    assert compute_token(KEY_A, "acct", "12345") != compute_token(KEY_A, "cust", "12345")


def test_different_values_differ() -> None:
    assert compute_token(KEY_A, "phone", "9000000001") != compute_token(
        KEY_A, "phone", "9000000002"
    )


@pytest.mark.parametrize("field_type", sorted(FIELD_TYPES))
def test_token_format_matches_the_event_schema_pattern(field_type: str) -> None:
    token = compute_token(KEY_A, field_type, "value")
    assert TOKEN_PATTERN.match(token)
    assert token_field_type(token) == field_type
    assert "value" not in token


def test_unknown_field_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown field type"):
        compute_token(KEY_A, "salary", "1")


@pytest.mark.parametrize("bad", ["ABCPT1234Z", "tok_pan_short", "tok_salary_" + "a" * 32, ""])
def test_non_tokens_are_rejected(bad: str) -> None:
    with pytest.raises(Invalid):
        token_field_type(bad)
