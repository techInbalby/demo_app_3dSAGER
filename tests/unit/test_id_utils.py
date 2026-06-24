"""Unit tests for lib/id_utils.py — building-ID normalization."""
import pytest

pytestmark = pytest.mark.unit


def test_extract_numeric_from_raw():
    from lib.id_utils import extract_numeric_id
    assert extract_numeric_id('0518100000271783') == '0518100000271783'


def test_extract_numeric_from_bag_prefix():
    from lib.id_utils import extract_numeric_id
    assert extract_numeric_id('bag_0518100000271783') == '0518100000271783'


def test_extract_numeric_from_full_bag_id():
    from lib.id_utils import extract_numeric_id
    assert extract_numeric_id('NL.IMBAG.Pand.0518100000271783-0') == '0518100000271783'


def test_extract_numeric_handles_none_and_empty():
    from lib.id_utils import extract_numeric_id
    assert extract_numeric_id(None) is None
    assert extract_numeric_id('') is None
    assert extract_numeric_id('no_digits_here') is None


def test_extract_numeric_requires_10_plus_digits():
    """Short numbers shouldn't false-positive as BAG ids."""
    from lib.id_utils import extract_numeric_id
    assert extract_numeric_id('id_42') is None
    assert extract_numeric_id('id_1234567890') == '1234567890'  # exactly 10 — accepted


def test_id_variants_from_raw_numeric():
    from lib.id_utils import id_variants
    variants = id_variants('0518100000271783')
    assert '0518100000271783' in variants
    assert 'bag_0518100000271783' in variants
    assert 'NL.IMBAG.Pand.0518100000271783-0' in variants


def test_id_variants_from_bag_prefix():
    from lib.id_utils import id_variants
    variants = id_variants('bag_0518100000271783')
    assert 'bag_0518100000271783' in variants
    assert '0518100000271783' in variants
    assert 'NL.IMBAG.Pand.0518100000271783-0' in variants


def test_id_variants_deduplicates():
    from lib.id_utils import id_variants
    variants = id_variants('0518100000271783')
    assert len(variants) == len(set(variants))


def test_id_variants_empty_for_none():
    from lib.id_utils import id_variants
    assert id_variants(None) == []


def test_numeric_ids_match_cross_variant():
    from lib.id_utils import numeric_ids_match
    assert numeric_ids_match('bag_0518100000271783', '0518100000271783')
    assert numeric_ids_match('NL.IMBAG.Pand.0518100000271783-0', 'bag_0518100000271783')


def test_numeric_ids_dont_match_different_buildings():
    from lib.id_utils import numeric_ids_match
    assert not numeric_ids_match('bag_0518100000271783', 'bag_0518100000209206')


def test_numeric_ids_match_with_no_digits_returns_false():
    from lib.id_utils import numeric_ids_match
    assert not numeric_ids_match('no_digits', 'still_none')
