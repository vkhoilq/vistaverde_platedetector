"""Plate Recognizer cloud OCR client plus image preprocessing helpers."""

from __future__ import annotations

import io
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests
from PIL import Image

__all__ = [
    "PlateResult",
    "PlateRecognizerError",
    "PlateRecognizerAuthError",
    "PlateRecognizerQuotaError",
    "PlateRecognizerBadImageError",
    "preprocess_image",
    "PlateRecognizerClient",
]

DEFAULT_API_URL = "https://api.platerecognizer.com/v1/plate-reader/"
DEFAULT_FIXTURE = Path(__file__).resolve().parent / "tests/fixtures/platerecognizer_response.json"

_TRUTHY = {"1", "true", "yes", "on"}

try:  # Pillow >= 9.1
    _LANCZOS = Image.Resampling.LANCZOS
except AttributeError:  # pragma: no cover - very old Pillow
    _LANCZOS = Image.LANCZOS


@dataclass
class PlateResult:
    plate: str
    score: float
    dscore: float
    region: str
    box: dict = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)


class PlateRecognizerError(Exception):
    """Any failure talking to (or understanding) Plate Recognizer."""


class PlateRecognizerAuthError(PlateRecognizerError):
    """Invalid/disabled token."""


class PlateRecognizerQuotaError(PlateRecognizerError):
    """Quota exhausted, rate limited or out of credit."""


class PlateRecognizerBadImageError(PlateRecognizerError):
    """The API rejected the uploaded image (HTTP 400)."""


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _region(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("code") or value.get("region") or ""
    if value is None:
        return ""
    return str(value)


def _candidates(value: Any) -> list[str]:
    if not value:
        return []
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            plate = item.get("plate")
        else:
            plate = item
        if plate:
            out.append(str(plate))
    return out


def preprocess_image(image_bytes: bytes, max_edge: int = 1920) -> bytes:
    """Downscale oversized images to ``max_edge`` and re-encode as JPEG.

    Returns the original bytes untouched when the image is small enough or
    cannot be opened by Pillow.
    """
    if not image_bytes:
        return image_bytes
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            width, height = img.size
            if max(width, height) <= max_edge:
                return image_bytes
            rgb = img.convert("RGB")
    except Exception:  # noqa: BLE001 - any decode failure means "pass through"
        return image_bytes

    try:
        scale = max_edge / float(max(width, height))
        new_size = (max(1, round(width * scale)), max(1, round(height * scale)))
        resized = rgb.resize(new_size, _LANCZOS)
        buffer = io.BytesIO()
        resized.save(buffer, format="JPEG", quality=85)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - never break capture because of encoding
        return image_bytes


def _test_mode() -> bool:
    return (os.getenv("PLATERECOGNIZER_TEST_MODE") or "").strip().lower() in _TRUTHY


def _fixture_path() -> Path:
    raw = (os.getenv("PLATERECOGNIZER_FIXTURE") or "").strip()
    if not raw:
        return DEFAULT_FIXTURE
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


class PlateRecognizerClient:
    """Thin synchronous wrapper around the Plate Recognizer plate-reader API."""

    def __init__(
        self,
        token: str,
        api_url: str = DEFAULT_API_URL,
        timeout: int = 30,
    ) -> None:
        self.token = (token or "").strip()
        self.api_url = api_url
        self.timeout = timeout

    # ----------------------------------------------------------------- public

    def recognize(self, image_bytes: bytes, filename: str = "plate.jpg") -> list[PlateResult]:
        """Recognise plates; results sorted by score descending (possibly empty)."""
        if _test_mode():
            return self._parse(self._load_fixture())

        headers = {"Authorization": f"Token {self.token}"}
        files = {"upload": (filename, image_bytes, "image/jpeg")}
        try:
            response = requests.post(
                self.api_url,
                files=files,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.Timeout:
            raise PlateRecognizerError(
                "Plate Recognizer request timeout: the service did not answer in time."
            ) from None
        except requests.HTTPError as exc:
            if exc.response is not None:
                self._raise_for_status(exc.response)
            raise PlateRecognizerError("Plate Recognizer request failed.") from None
        except requests.RequestException:
            raise PlateRecognizerError(
                "Plate Recognizer request failed (network error)."
            ) from None

        self._raise_for_status(response)

        try:
            payload = response.json()
        except ValueError:
            raise PlateRecognizerError(
                "Plate Recognizer returned a response that was not valid JSON."
            ) from None
        return self._parse(payload)

    # ---------------------------------------------------------------- private

    def _load_fixture(self) -> dict:
        path = _fixture_path()
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise PlateRecognizerError(
                f"Could not read Plate Recognizer fixture at {path}: {exc}"
            ) from None
        try:
            payload = json.loads(text)
        except ValueError as exc:
            raise PlateRecognizerError(
                f"Plate Recognizer fixture at {path} is not valid JSON: {exc}"
            ) from None
        if not isinstance(payload, dict):
            raise PlateRecognizerError(
                f"Plate Recognizer fixture at {path} must contain a JSON object."
            )
        return payload

    def _parse(self, payload: Any) -> list[PlateResult]:
        if not isinstance(payload, dict):
            return []
        raw_results = payload.get("results") or []
        if not isinstance(raw_results, (list, tuple)):
            return []

        results: list[PlateResult] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            box = item.get("box")
            plate = item.get("plate")
            results.append(
                PlateResult(
                    plate="" if plate is None else str(plate),
                    score=_float(item.get("score")),
                    dscore=_float(item.get("dscore")),
                    region=_region(item.get("region")),
                    box=box if isinstance(box, dict) else {},
                    candidates=_candidates(item.get("candidates")),
                )
            )
        results.sort(key=lambda result: result.score, reverse=True)
        return results

    def _raise_for_status(self, response: requests.Response) -> None:
        status = getattr(response, "status_code", 200) or 200
        if status < 400:
            return

        if status == 400:
            raise PlateRecognizerBadImageError(
                "Plate Recognizer rejected the image (HTTP 400). "
                "Try a clearer or smaller photo."
            )

        if status == 401:
            raise PlateRecognizerAuthError(
                "Plate Recognizer authentication failed (HTTP 401). "
                "Check PLATERECOGNIZER_TOKEN."
            )

        if status == 403:
            body = (getattr(response, "text", "") or "").lower()
            if any(word in body for word in ("quota", "balance", "limit", "credit")):
                raise PlateRecognizerQuotaError(
                    "Plate Recognizer quota or balance exhausted (HTTP 403)."
                )
            raise PlateRecognizerAuthError(
                "Plate Recognizer denied the request (HTTP 403). Check the token."
            )

        if status == 429:
            raise PlateRecognizerQuotaError(
                "Plate Recognizer rate limit reached (HTTP 429). Try again shortly."
            )

        if 500 <= status < 600:
            raise PlateRecognizerError(
                f"Plate Recognizer service error (HTTP {status}). Try again later."
            )

        raise PlateRecognizerError(f"Plate Recognizer request failed (HTTP {status}).")
