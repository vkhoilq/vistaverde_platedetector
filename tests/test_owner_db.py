"""Tests for owner_db (loading, exact lookup, suggestions, hot reload)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import plate_normalize as pn
from owner_db import Owner, OwnerDB

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CSV = REPO_ROOT / "data" / "owners.csv"

SAMPLE_CSV = """plate,unit_no
51F-123.45,B-12
30A 45678,A-01
52B333.44,C-03
59H1-234.56,Tower 2 - 05
"""


def write_csv(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def db(tmp_path: Path) -> OwnerDB:
    path = tmp_path / "owners.csv"
    write_csv(path, SAMPLE_CSV)
    return OwnerDB(path)


# ----------------------------------------------------------------------- basics

def test_loads_rows(db):
    assert len(db) == 4


def test_owner_fields(db):
    owner = db.lookup("51F-123.45")
    assert isinstance(owner, Owner)
    assert owner.plate == "51F-123.45"
    assert owner.unit_no == "B-12"
    assert owner.key == "51F12345"


def test_header_tolerates_case_and_whitespace(tmp_path):
    path = tmp_path / "owners.csv"
    write_csv(path, " Plate , UNIT_NO \n 51f 123.45 , B-12 \n")
    db = OwnerDB(path)
    assert len(db) == 1
    owner = db.lookup("51F12345")
    assert owner is not None
    assert owner.unit_no == "B-12"


def test_blank_rows_and_blank_plates_skipped(tmp_path):
    path = tmp_path / "owners.csv"
    write_csv(path, "plate,unit_no\n\n,EMPTY\n51F-123.45,B-12\n   ,X\n")
    db = OwnerDB(path)
    assert len(db) == 1


def test_duplicate_canonical_keeps_first_and_warns(tmp_path, capsys):
    path = tmp_path / "owners.csv"
    write_csv(path, "plate,unit_no\n51F-123.45,B-12\n51f12345,B-99\n")
    db = OwnerDB(path)
    assert len(db) == 1
    assert db.lookup("51F12345").unit_no == "B-12"
    assert "duplicate" in capsys.readouterr().err.lower()


# ----------------------------------------------------------------------- lookup

def test_lookup_across_formatting(db):
    for variant in ("51F-123.45", "51f 123.45", "51F.123.45", "51F12345", " 51f-123.45 "):
        owner = db.lookup(variant)
        assert owner is not None, variant
        assert owner.unit_no == "B-12"


def test_lookup_unknown_returns_none(db):
    assert db.lookup("99Z99999") is None
    assert db.lookup("") is None


def test_lookup_never_fuzzy(db):
    # 51F1234S is one confusion away from 51F12345 but is not in the db.
    assert db.lookup("51F1234S") is None


# ------------------------------------------------------------------- suggestions

def test_suggestions_find_confusion_neighbour(db):
    results = db.suggestions("51F1234S")
    assert results, "expected at least one fuzzy suggestion"
    owner, score = results[0]
    assert owner.unit_no == "B-12"
    assert score == pytest.approx(1 - 0.5 / 8)
    assert pn.is_close("51F1234S", owner.key)


def test_suggestions_respect_limit_and_sorting(db):
    results = db.suggestions("51F12345", limit=2)
    assert 0 < len(results) <= 2
    scores = [score for _owner, score in results]
    assert scores == sorted(scores, reverse=True)
    assert len(db.suggestions("51F12345", limit=0)) == 0


def test_suggestions_empty_for_unrelated_plate(db):
    assert db.suggestions("99Z99999") == []


# ------------------------------------------------------------------ maybe_reload

def test_maybe_reload_false_when_unchanged(db):
    assert db.maybe_reload() is False
    assert db.maybe_reload() is False


def test_maybe_reload_true_after_rewrite(db, tmp_path):
    path = db.path
    write_csv(path, "plate,unit_no\n77H-888.99,D-77\n")
    # Force a distinct mtime even on coarse-grained filesystems.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert db.maybe_reload() is True
    assert len(db) == 1
    assert db.lookup("77H-888.99").unit_no == "D-77"
    assert db.lookup("51F-123.45") is None


def test_missing_file_is_empty(tmp_path):
    db = OwnerDB(tmp_path / "does-not-exist.csv")
    assert len(db) == 0
    assert db.lookup("51F12345") is None
    assert db.maybe_reload() is False


def test_file_reappears_after_missing(tmp_path):
    path = tmp_path / "owners.csv"
    db = OwnerDB(path)
    write_csv(path, SAMPLE_CSV)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert db.maybe_reload() is True
    assert len(db) == 4


def test_reload_raises_clear_error_on_malformed(tmp_path):
    path = tmp_path / "owners.csv"
    write_csv(path, "unit_of_building,owner_name\nA-01,Nguyen\n")
    with pytest.raises(RuntimeError) as excinfo:
        OwnerDB(path)
    assert "owners.csv" in str(excinfo.value)


# ------------------------------------------------------------- real dataset file

def test_real_owners_csv_exists_and_is_nonempty():
    assert REAL_CSV.exists(), f"missing {REAL_CSV}"
    db = OwnerDB(REAL_CSV)
    assert 15 <= len(db) <= 30


def test_real_owners_csv_has_unique_canonical_keys():
    assert REAL_CSV.exists()
    import csv

    with REAL_CSV.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        keys = [pn.canonical((row.get("plate") or "").strip()) for row in reader]

    assert all(keys), "every row must have a non-empty plate"
    assert len(keys) == len(set(keys)), "duplicate canonical plate keys in data/owners.csv"


def test_real_dataset_has_confusion_pair_suggestions():
    db = OwnerDB(REAL_CSV)
    results = db.suggestions("51F1234S", limit=3)
    units = [owner.unit_no for owner, _score in results]
    assert "B-12" in units
