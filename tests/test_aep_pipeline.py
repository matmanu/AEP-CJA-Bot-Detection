"""
Lightweight tests for bot_detection.aep_pipeline: pure logic only (dataset
name validation, SQL composition). No real AEP connection is used or
needed here — that part can only be tested against a live instance.

Requires the 'aep' extra (psycopg2-binary): these tests import
bot_detection.aep_pipeline, which has a hard dependency on it.

    pytest tests/test_aep_pipeline.py -v
"""

import pytest

from bot_detection.aep_pipeline import (
    DATASET_SUFFIX,
    DEFAULT_BOT_TABLE,
    EXTRACTION_LIMIT,
    XDM_NAMESPACE,
    _validate_dataset_name,
    build_single_dataset_query,
    build_union_query,
)


def test_validate_dataset_name_accepts_clean_names():
    assert _validate_dataset_name("example_dataset_1") == "example_dataset_1"
    assert _validate_dataset_name("  Example_Dataset  ") == "example_dataset"


def test_validate_dataset_name_rejects_sql_injection_attempts():
    with pytest.raises(ValueError):
        _validate_dataset_name("bad; DROP TABLE x")


def test_build_union_query_composes_without_error():
    query = build_union_query(["example_dataset_1", "example_dataset_2"], days=20, bot_table=DEFAULT_BOT_TABLE)
    # A Composed object — full rendering requires a real connection (for
    # proper identifier quoting), but successful construction alone already
    # confirms every sql.Identifier/sql.Literal/sql.SQL call type-checked.
    assert query is not None


def test_build_union_query_rejects_non_positive_days():
    with pytest.raises(ValueError):
        build_union_query(["example_dataset_1"], days=0, bot_table=DEFAULT_BOT_TABLE)


def test_build_single_dataset_query_composes_without_error():
    query = build_single_dataset_query("example_dataset_1", days=20)
    assert query is not None


def test_xdm_namespace_is_a_non_empty_placeholder():
    """Sanity check that the namespace constant hasn't been left empty —
    an empty namespace would silently produce malformed field paths."""
    assert XDM_NAMESPACE
    assert isinstance(XDM_NAMESPACE, str)


def test_extraction_limit_is_generous():
    """AEP Query Service silently caps at 50,000 rows without an explicit
    LIMIT — this should be comfortably above any real result set."""
    assert EXTRACTION_LIMIT >= 1_000_000
