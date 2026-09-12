from pathlib import Path

import pytest

from juried.criteria import CriteriaError, load_criteria, parse_criteria, slugify

DOCUMENT = """# Support bot acceptance criteria

Intro text that is not a criterion.

## Opening hours
The bot tells customers the opening hours, Monday to Friday, 9am to 5pm.

## Refund policy {#refunds}
Customers can return items within 14 days.

Second paragraph.

## Stays polite
"""


def test_parse_headings_into_criteria() -> None:
    criteria = parse_criteria(DOCUMENT)
    assert [c.id for c in criteria] == ["opening-hours", "refunds", "stays-polite"]
    assert criteria[0].title == "Opening hours"
    assert criteria[0].description.startswith("The bot tells customers")
    assert criteria[1].title == "Refund policy"
    assert "Second paragraph." in criteria[1].description
    assert criteria[2].description == ""


def test_slugify() -> None:
    assert slugify("  Handles UK post codes! ") == "handles-uk-post-codes"


def test_duplicate_ids_rejected() -> None:
    with pytest.raises(CriteriaError, match="duplicate"):
        parse_criteria("## Same\n## same\n")


def test_load_requires_at_least_one(tmp_path: Path) -> None:
    path = tmp_path / "acceptance.md"
    path.write_text("# Title only\n")
    with pytest.raises(CriteriaError, match="no criteria"):
        load_criteria(path)
    with pytest.raises(CriteriaError, match="not found"):
        load_criteria(tmp_path / "missing.md")
