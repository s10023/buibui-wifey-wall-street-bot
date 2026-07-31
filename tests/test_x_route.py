import pytest

from tools.x_route import route_target


@pytest.mark.parametrize(
    "content_type, verdict, expected",
    [
        ("setup", "NOVEL", "docs/plans/pundit-calls.jsonl"),
        ("mechanic", "NOVEL", "docs/plans/mechanics-backlog.md"),
        ("claim", "NOVEL", "docs/plans/thesis-inbox.md"),
        ("claim", "ALREADY-TESTED", None),
        ("claim", "FROZEN-CATEGORY", None),
        ("claim", "NOT-FALSIFIABLE", None),
    ],
)
def test_route_target(content_type: str, verdict: str, expected: str | None) -> None:
    assert route_target(content_type, verdict) == expected


def test_route_target_unroutable() -> None:
    with pytest.raises(ValueError):
        route_target("claim", "BOGUS")
