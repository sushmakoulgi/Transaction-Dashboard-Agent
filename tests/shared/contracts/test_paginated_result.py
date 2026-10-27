import pytest
from pydantic import ValidationError

from shared.contracts import FilterIssueCode, FilterValidationIssue, PaginatedResult


@pytest.mark.parametrize(
    ("total_count", "page_size", "expected_total_pages"),
    [(0, 25, 0), (1, 25, 1), (25, 25, 1), (51, 25, 3)],
)
def test_total_pages_rounds_up(total_count: int, page_size: int, expected_total_pages: int) -> None:
    result = PaginatedResult[str](items=[], page=1, page_size=page_size, total_count=total_count)
    assert result.total_pages == expected_total_pages


def test_total_pages_is_included_in_serialized_output() -> None:
    result = PaginatedResult[str](items=["a"], page=1, page_size=25, total_count=1)
    assert result.model_dump() == {
        "items": ["a"],
        "page": 1,
        "page_size": 25,
        "total_count": 1,
        "total_pages": 1,
    }


def test_rejects_negative_total_count() -> None:
    with pytest.raises(ValidationError):
        PaginatedResult[str](items=[], page=1, page_size=25, total_count=-1)


def test_validation_issue_serializes_with_code_and_suggestions() -> None:
    issue = FilterValidationIssue(
        path="filter.children[0].field",
        code=FilterIssueCode.UNKNOWN_FIELD,
        message="unknown field 'cust_id'",
        suggestions=["customer_id"],
    )
    assert issue.model_dump(mode="json")["code"] == "UNKNOWN_FIELD"


def test_serialized_result_can_be_read_back() -> None:
    result = PaginatedResult[str](items=["a"], page=1, page_size=25, total_count=51)
    assert PaginatedResult[str].model_validate(result.model_dump(mode="json")) == result


def test_rejects_a_total_pages_that_does_not_match() -> None:
    with pytest.raises(ValidationError, match="total_pages 9 does not match"):
        PaginatedResult[str].model_validate(
            {"items": [], "page": 1, "page_size": 25, "total_count": 51, "total_pages": 9}
        )
