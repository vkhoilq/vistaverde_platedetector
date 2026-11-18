"""Tests for plate_normalize (canonical forms, display form, fuzzy distance)."""

from __future__ import annotations

import pytest

import plate_normalize as pn


# --------------------------------------------------------------------- canonical

@pytest.mark.parametrize(
    "raw",
    [
        "51F-123.45",
        "51F 12345",
        "51F.123.45",
        "51f12345",
        " 51f-123.45 ",
        "51F_123.45",
        "51F—123·45",
        "51F12345",
    ],
)
def test_canonical_equivalence(raw):
    assert pn.canonical(raw) == "51F12345"


def test_canonical_translates_d_stroke():
    assert pn.canonical("đ") == "D"
    assert pn.canonical("Đ") == "D"
    assert pn.canonical("51đ-123.45") == "51D12345"


def test_canonical_empty_inputs():
    assert pn.canonical("") == ""
    assert pn.canonical("   - . ") == ""
    assert pn.canonical(None) == ""


# --------------------------------------------------------------------- format_vn

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("51F12345", "51F-123.45"),
        ("51f-123.45", "51F-123.45"),
        ("51F 12345", "51F-123.45"),
        ("59H123456", "59H1-234.56"),
        ("59h-123.456", "59H1-234.56"),
    ],
)
def test_format_vn_eight_and_nine(raw, expected):
    assert pn.format_vn(raw) == expected


@pytest.mark.parametrize("raw", ["1234567", "51F1234567", "", "abc"])
def test_format_vn_other_lengths_return_canonical(raw):
    assert pn.format_vn(raw) == pn.canonical(raw)


def test_format_vn_is_idempotent():
    once = pn.format_vn("51F12345")
    assert pn.format_vn(once) == once


# --------------------------------------------------------------------- distance

def test_identical_distance_is_zero():
    assert pn.distance("51F-123.45", "51f12345") == 0.0
    assert pn.similarity("51F-123.45", "51f12345") == 1.0


def test_confusion_substitution_costs_half():
    assert pn.distance("51F12345", "51F1234S") == pytest.approx(0.5)
    assert pn.distance("51F12345", "51F1234Z") == pytest.approx(1.0)


def test_empty_distances():
    assert pn.distance("", "51F12345") == 8.0
    assert pn.distance("", "") == 0.0
    assert pn.similarity("", "") == 1.0
    assert pn.similarity("", "51F12345") == 0.0
    assert pn.similarity("51F12345", "") == 0.0


# ------------------------------------------------------------------ similarity

@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("51F12345", "51F1234S"),
        ("51F12345", "51F12435"),
        ("30A45678", "30A12345"),
        ("", "51F12345"),
        ("51F-123.45", "51f 123.45"),
    ],
)
def test_similarity_bounds_and_symmetry(a, b):
    forward = pn.similarity(a, b)
    backward = pn.similarity(b, a)
    assert 0.0 <= forward <= 1.0
    assert forward == pytest.approx(backward)


def test_similarity_confusion_beats_plain_edit():
    confusion = pn.similarity("51F12345", "51F1234S")
    plain = pn.similarity("51F12345", "51F1234Z")
    assert 0.5 < confusion < 1.0
    assert confusion > plain


# -------------------------------------------------------------------- is_close

def test_is_close_true_for_confusion_pair():
    assert pn.is_close("51F12345", "51F-123.4S") is True


def test_is_close_false_for_different_plate():
    assert pn.is_close("51F12345", "51F99999") is False
    assert pn.is_close("51F12345", "51F12345") is True


def test_is_close_respects_max_distance():
    # Two independent edits of cost 1.0 each.
    assert pn.is_close("51F12345", "51F1234S", max_distance=0.5) is True
    assert pn.is_close("51F12345", "51F1234Z", max_distance=0.5) is False
    assert pn.is_close("51F12345", "51F1234Z", max_distance=1) is True
    assert pn.is_close("51F12345", "51F12X4Z", max_distance=1) is False
    assert pn.is_close("51F12345", "51F12X4Z", max_distance=2) is True
