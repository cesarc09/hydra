import pytest

from server.services.skills import markers, render, validate


@pytest.mark.parametrize(
    "common, expected",
    [
        ("{{x}} {{two_words}} {{a1}}", {"x", "two_words", "a1"}),
        ("{{Bad}} {{ x }} {{1x}} {{has-dash}}", set()),
        ("{{x}}{{y}}", {"x", "y"}),
        ("{{{x}}} {{{{y}}}} {{outer_{{inner}}}}", {"x", "y", "inner"}),
    ],
)
def test_markers(common: str, expected: set[str]):
    assert markers(common) == expected


def test_render_without_variant_is_verbatim():
    common = "Use {{tool}} and keep {{Bad}} literal."
    assert render(common, None) == common


def test_render_substitutes_once():
    assert render("{{x}} + {{y}}", {"x": "{{y}}", "y": "done"}) == "{{y}} + done"


def test_validate_names_harness_and_first_missing_slot():
    with pytest.raises(ValueError, match="codex-cli.*alpha"):
        validate("{{zeta}} {{alpha}}", {"codex-cli": {}})


def test_validate_allows_extra_keys():
    validate("{{needed}}", {"claude-code": {"needed": "yes", "extra": "ok"}})


def test_no_variant_harness_needs_no_validation():
    common = "literal until configured: {{slot}}"
    validate(common, {})
    assert render(common, None) == common
