"""Complaint draft construction and plain-text rendering."""

from __future__ import annotations

from datetime import datetime, timezone

from plate_normalize import format_vn

__all__ = ["build_complaint", "render_complaint_text"]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_complaint(
    plate: str,
    unit_no: str | None,
    ocr_score: float | None,
    confirmed_by: str,
    image_bytes: bytes | None = None,
) -> dict:
    """Build the complaint draft dict consumed by the UI and ``render_complaint_text``."""
    image_present = bool(image_bytes)
    return {
        "plate": format_vn(plate),
        "unit_no": unit_no,
        "ocr_score": _as_float(ocr_score),
        "confirmed_by": confirmed_by or "operator",
        "created_at": _utc_now_iso(),
        "image_present": image_present,
    }


def render_complaint_text(complaint: dict) -> str:
    """Compact multi-line plain-text version of a complaint draft."""
    plate = format_vn(complaint.get("plate") or "")
    unit_no = complaint.get("unit_no")
    score = complaint.get("ocr_score")
    confirmed_by = complaint.get("confirmed_by") or "operator"
    created_at = complaint.get("created_at") or _utc_now_iso()

    lines = [
        "COMPLAINT DRAFT",
        f"Plate: {plate or 'N/A'}",
        f"Unit: {unit_no if unit_no else 'Unknown'}",
        f"Recognised score: {'n/a' if score is None else f'{float(score):.2f}'}",
        f"Confirmed by: {confirmed_by}",
        f"Created: {created_at}",
    ]
    return "\n".join(lines)
