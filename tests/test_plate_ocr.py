"""Tests for plate_ocr (fixture mode, preprocessing, API error mapping)."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
import requests
from PIL import Image

import plate_ocr
from plate_ocr import (
    PlateRecognizerAuthError,
    PlateRecognizerBadImageError,
    PlateRecognizerClient,
    PlateRecognizerError,
    PlateRecognizerQuotaError,
    PlateResult,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "platerecognizer_response.json"

TOKEN = "secret-test-token-do-not-leak"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PLATERECOGNIZER_TEST_MODE", raising=False)
    monkeypatch.delenv("PLATERECOGNIZER_FIXTURE", raising=False)


def make_image_bytes(size=(4000, 3000), fmt="PNG") -> bytes:
    image = Image.new("RGB", size, (120, 130, 140))
    buffer = io.BytesIO()
    image.save(buffer, format=fmt)
    return buffer.getvalue()


# ---------------------------------------------------------------- fixture mode

def test_fixture_file_is_valid_json():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert len(payload["results"]) == 2


def test_test_mode_returns_two_sorted_results(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "1")
    monkeypatch.setenv("PLATERECOGNIZER_FIXTURE", str(FIXTURE))

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("network call attempted in test mode")

    monkeypatch.setattr(plate_ocr.requests, "post", explode)

    client = PlateRecognizerClient(TOKEN)
    results = client.recognize(b"fake-image-bytes", filename="plate.jpg")

    assert isinstance(results, list)
    assert len(results) == 2
    assert all(isinstance(result, PlateResult) for result in results)
    scores = [result.score for result in results]
    assert scores == sorted(scores, reverse=True)
    assert results[0].plate == "51F12345"
    assert results[0].region == "vn"
    assert results[0].score == pytest.approx(0.943)
    assert results[0].dscore == pytest.approx(0.901)
    assert isinstance(results[0].box, dict) and results[0].box
    assert isinstance(results[0].candidates, list) and results[0].candidates
    assert results[1].score < results[0].score


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", " Yes "])
def test_test_mode_truthy_values(monkeypatch, value):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", value)
    monkeypatch.setenv("PLATERECOGNIZER_FIXTURE", str(FIXTURE))
    assert len(PlateRecognizerClient(TOKEN).recognize(b"x")) == 2


def test_test_mode_reads_env_at_call_time(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_FIXTURE", str(FIXTURE))
    client = PlateRecognizerClient(TOKEN)  # constructed while mode is off
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "1")

    monkeypatch.setattr(
        plate_ocr.requests,
        "post",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("network call")),
    )
    assert len(client.recognize(b"x")) == 2


def test_test_mode_default_fixture_path(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "1")
    assert len(PlateRecognizerClient(TOKEN).recognize(b"x")) == 2


# ------------------------------------------------------------- preprocess_image

def test_preprocess_shrinks_oversized_image_and_keeps_aspect():
    original = make_image_bytes((4000, 3000))
    processed = plate_ocr.preprocess_image(original, max_edge=1920)

    assert processed != original
    with Image.open(io.BytesIO(processed)) as img:
        assert img.format == "JPEG"
        width, height = img.size
    assert max(width, height) == 1920
    assert width == 1920 and height == 1440
    assert width / height == pytest.approx(4000 / 3000, rel=1e-3)


def test_preprocess_leaves_small_image_untouched():
    original = make_image_bytes((320, 200))
    assert plate_ocr.preprocess_image(original, max_edge=1920) == original


def test_preprocess_returns_original_for_garbage():
    garbage = b"definitely not an image\x00\x01\x02"
    assert plate_ocr.preprocess_image(garbage) == garbage


def test_preprocess_handles_empty_bytes():
    assert plate_ocr.preprocess_image(b"") == b""


def test_preprocess_respects_custom_max_edge():
    processed = plate_ocr.preprocess_image(make_image_bytes((1000, 500)), max_edge=400)
    with Image.open(io.BytesIO(processed)) as img:
        assert img.size == (400, 200)


# --------------------------------------------------------------- error mapping

class FakeResponse:
    def __init__(self, status_code: int, text: str = "", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def raise_http_error(monkeypatch, response: FakeResponse):
    def fake_post(*args, **kwargs):
        raise requests.HTTPError("boom", response=response)

    monkeypatch.setattr(plate_ocr.requests, "post", fake_post)


def test_401_maps_to_auth_error(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    raise_http_error(monkeypatch, FakeResponse(401, "unauthorized"))
    with pytest.raises(PlateRecognizerAuthError) as excinfo:
        PlateRecognizerClient(TOKEN).recognize(b"img")
    assert TOKEN not in str(excinfo.value)


def test_403_quota_body_maps_to_quota_error(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    raise_http_error(monkeypatch, FakeResponse(403, '{"error": "quota exceeded"}'))
    with pytest.raises(PlateRecognizerQuotaError) as excinfo:
        PlateRecognizerClient(TOKEN).recognize(b"img")
    assert TOKEN not in str(excinfo.value)


def test_403_balance_body_maps_to_quota_error(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    raise_http_error(monkeypatch, FakeResponse(403, "insufficient balance"))
    with pytest.raises(PlateRecognizerQuotaError):
        PlateRecognizerClient(TOKEN).recognize(b"img")


def test_403_without_quota_body_maps_to_auth_error(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    raise_http_error(monkeypatch, FakeResponse(403, "forbidden"))
    with pytest.raises(PlateRecognizerAuthError):
        PlateRecognizerClient(TOKEN).recognize(b"img")


def test_429_maps_to_quota_error(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    raise_http_error(monkeypatch, FakeResponse(429, "too many requests"))
    with pytest.raises(PlateRecognizerQuotaError):
        PlateRecognizerClient(TOKEN).recognize(b"img")


def test_400_maps_to_bad_image_error(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    raise_http_error(monkeypatch, FakeResponse(400, '{"error": "bad image"}'))
    with pytest.raises(PlateRecognizerBadImageError):
        PlateRecognizerClient(TOKEN).recognize(b"img")


def test_500_maps_to_generic_error(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    raise_http_error(monkeypatch, FakeResponse(500, "server error"))
    with pytest.raises(PlateRecognizerError) as excinfo:
        PlateRecognizerClient(TOKEN).recognize(b"img")
    assert not isinstance(excinfo.value, (PlateRecognizerAuthError, PlateRecognizerQuotaError))
    assert TOKEN not in str(excinfo.value)


def test_timeout_maps_to_error_mentioning_timeout(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")

    def fake_post(*args, **kwargs):
        raise requests.Timeout("timed out")

    monkeypatch.setattr(plate_ocr.requests, "post", fake_post)
    with pytest.raises(PlateRecognizerError) as excinfo:
        PlateRecognizerClient(TOKEN).recognize(b"img")
    assert "timeout" in str(excinfo.value).lower()
    assert TOKEN not in str(excinfo.value)


def test_other_request_exception_maps_to_error(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")

    def fake_post(*args, **kwargs):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(plate_ocr.requests, "post", fake_post)
    with pytest.raises(PlateRecognizerError):
        PlateRecognizerClient(TOKEN).recognize(b"img")


# ------------------------------------------------------------- live-mode parsing

def test_live_mode_empty_results_returns_empty_list(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    monkeypatch.setattr(
        plate_ocr.requests,
        "post",
        lambda *a, **k: FakeResponse(200, "", {"results": []}),
    )
    assert PlateRecognizerClient(TOKEN).recognize(b"img") == []


def test_live_mode_missing_results_returns_empty_list(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    monkeypatch.setattr(
        plate_ocr.requests,
        "post",
        lambda *a, **k: FakeResponse(200, "", {}),
    )
    assert PlateRecognizerClient(TOKEN).recognize(b"img") == []


def test_live_mode_uses_expected_request_shape(monkeypatch):
    monkeypatch.setenv("PLATERECOGNIZER_TEST_MODE", "0")
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse(
            200,
            "",
            {
                "results": [
                    {"plate": "51F12345", "score": 0.8},
                    {"plate": "30A45678", "score": 0.95},
                ]
            },
        )

    monkeypatch.setattr(plate_ocr.requests, "post", fake_post)
    client = PlateRecognizerClient(TOKEN, timeout=12)
    results = client.recognize(b"img", filename="car.png")

    assert captured["url"] == plate_ocr.DEFAULT_API_URL
    assert captured["headers"]["Authorization"] == f"Token {TOKEN}"
    assert "upload" in captured["files"]
    assert captured["timeout"] == 12
    assert "params" not in captured
    assert [result.plate for result in results] == ["30A45678", "51F12345"]
    # Missing fields fall back to safe defaults.
    assert results[1].region == ""
    assert results[1].dscore == 0.0
    assert results[1].box == {}
    assert results[1].candidates == []
