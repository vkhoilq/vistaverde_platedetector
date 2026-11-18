"""Streamlit UI for filing a vehicle complaint from a license-plate photo.

The backend modules (plate_normalize, owner_db, plate_ocr, complaint) are already
implemented and tested. This file only contains the user interface.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from complaint import build_complaint, render_complaint_text
from owner_db import Owner, OwnerDB, OwnerDBError
from plate_normalize import canonical, format_vn
from plate_ocr import (
    PlateRecognizerAuthError,
    PlateRecognizerBadImageError,
    PlateRecognizerClient,
    PlateRecognizerError,
    PlateRecognizerQuotaError,
    preprocess_image,
)

# Load environment variables from .env before anything else. On Streamlit
# Community Cloud there is no .env file; settings come from the app's secrets.
load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent
OWNERS_CSV = PROJECT_DIR / "data" / "owners.csv"


def _setting(name: str) -> str:
    """Read a setting from Streamlit secrets, falling back to the environment.

    Community Cloud supplies configuration through the app's secrets dashboard,
    while local and Docker runs use ``.env``. ``st.secrets`` raises
    ``FileNotFoundError`` when no secrets file exists, so the lookup is guarded.
    """
    try:
        value = st.secrets.get(name)
    except Exception:  # noqa: BLE001 - no secrets file, or an unreadable one
        value = None
    if value is None:
        value = os.getenv(name)
    if value is None:
        return ""
    return str(value).strip()


PLATERECOGNIZER_TOKEN = _setting("PLATERECOGNIZER_TOKEN")
PLATERECOGNIZER_TEST_MODE = _setting("PLATERECOGNIZER_TEST_MODE").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

OCR_CONFIDENCE_GOOD = 0.80
OCR_CONFIDENCE_WARN = 0.60
OCR_CANDIDATE_THRESHOLD = 0.50


# ---------------------------------------------------------------------------
# Cached owner database
# ---------------------------------------------------------------------------


@st.cache_data(show_spinner=False)
def _load_owner_db(path: str, mtime_ns: int | None) -> OwnerDB:
    """Load the owner CSV; the mtime argument makes the cache invalidate on change."""
    return OwnerDB(path)


def get_owner_db() -> OwnerDB | None:
    """Return a hot-reloading OwnerDB, or None if the file is unreachable."""
    try:
        mtime_ns = OWNERS_CSV.stat().st_mtime_ns
    except FileNotFoundError:
        mtime_ns = None
    except OSError as exc:
        st.error(f"Cannot access owner database at {OWNERS_CSV}: {exc}")
        return None

    try:
        db = _load_owner_db(str(OWNERS_CSV), mtime_ns)
        db.maybe_reload()
        return db
    except OwnerDBError as exc:
        st.error(f"Owner database problem: {exc}")
        return None


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------


def _session_defaults() -> dict[str, object]:
    return {
        "step": "capture",
        "image_bytes": None,
        "image_hash": None,
        "ocr_results": None,
        "selected_plate": None,
        "ocr_score": None,
        "ocr_region": None,
        "confirmed_plate": "",
        "owner": None,
        "searched_plate": None,
        "complaint": None,
        "filed_complaints": [],
        "last_ocr_hash": None,
        "last_error": None,
        "error_detail": None,
        "manual_plate": "",
    }


def init_session_state() -> None:
    for key, value in _session_defaults().items():
        st.session_state.setdefault(key, value)


def reset_flow(keep_filed: bool = True) -> None:
    filed = st.session_state.get("filed_complaints", [])
    defaults = _session_defaults()
    for key, value in defaults.items():
        st.session_state[key] = value
    if keep_filed:
        st.session_state["filed_complaints"] = filed


def go_capture() -> None:
    reset_flow(keep_filed=True)
    st.rerun()


def go_confirm() -> None:
    plate = st.session_state.get("selected_plate") or ""
    st.session_state["confirmed_plate"] = format_vn(plate)
    st.session_state["step"] = "confirm"
    st.rerun()


def go_result(owner: Owner | None, searched_plate: str) -> None:
    st.session_state["owner"] = owner
    st.session_state["searched_plate"] = searched_plate
    st.session_state["complaint"] = None
    st.session_state["step"] = "result"
    st.rerun()


# ---------------------------------------------------------------------------
# OCR helpers
# ---------------------------------------------------------------------------


def _image_hash(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def _ocr_client() -> PlateRecognizerClient:
    token = PLATERECOGNIZER_TOKEN or ("dummy" if PLATERECOGNIZER_TEST_MODE else "")
    return PlateRecognizerClient(token=token)


def _friendly_ocr_message(exc: PlateRecognizerError) -> tuple[str, str]:
    """Return (user-facing message, technical detail) for an OCR error."""
    detail = str(exc)
    if isinstance(exc, PlateRecognizerAuthError):
        return (
            "Token problem: the Plate Recognizer API did not accept the configured token.",
            detail,
        )
    if isinstance(exc, PlateRecognizerQuotaError):
        return (
            "Quota or limit reached on the Plate Recognizer account.",
            detail,
        )
    if isinstance(exc, PlateRecognizerBadImageError):
        return (
            "Bad photo: the API rejected the image. Try a closer, straighter shot with better light.",
            detail,
        )
    return (
        "Plate Recognizer service is unavailable. Please try again in a moment.",
        detail,
    )


def run_ocr(image_bytes: bytes) -> None:
    """Run OCR once per unique image and store the results in session state."""
    img_hash = _image_hash(image_bytes)

    # Guard against duplicate API calls for the same image.
    if st.session_state.get("last_ocr_hash") == img_hash and st.session_state.get(
        "ocr_results"
    ) is not None:
        return

    st.session_state["last_error"] = None
    st.session_state["error_detail"] = None

    try:
        processed = preprocess_image(image_bytes)
        client = _ocr_client()
        results = client.recognize(processed)
        st.session_state["last_ocr_hash"] = img_hash
        st.session_state["ocr_results"] = results
    except PlateRecognizerError as exc:
        message, detail = _friendly_ocr_message(exc)
        st.session_state["last_error"] = message
        st.session_state["error_detail"] = detail


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def _read_uploaded(file) -> bytes | None:
    if file is None:
        return None
    return file.getvalue()


def _confidence_band(score: float | None) -> tuple[str, str, str]:
    """Return (label, colour, icon) for an OCR confidence score."""
    if score is None:
        return ("Confirmed by operator", "#6b7280", "✓")
    if score >= OCR_CONFIDENCE_GOOD:
        return (f"OCR confidence {int(score * 100)}%", "#16a34a", "✓")
    if score >= OCR_CONFIDENCE_WARN:
        return (f"OCR confidence {int(score * 100)}%", "#ca8a04", "⚠")
    return (f"OCR confidence {int(score * 100)}%", "#dc2626", "!")


def _badge(text: str, colour: str, icon: str) -> None:
    st.markdown(
        f"""
        <span style="
            display:inline-block;
            padding:0.25rem 0.6rem;
            border-radius:999px;
            background-color:{colour}15;
            color:{colour};
            font-weight:600;
            font-size:0.9rem;
            border:1px solid {colour}40;">
            {icon}&nbsp;{text}
        </span>
        """,
        unsafe_allow_html=True,
    )


def _apply_css() -> None:
    st.markdown(
        """
        <style>
            .block-container {
                max-width: 760px;
                padding: 1rem;
            }
            h1 {
                font-size: 1.75rem !important;
                margin-bottom: 0.25rem !important;
            }
            h2, h3 {
                font-size: 1.25rem !important;
                margin-top: 1rem !important;
            }
            .stCaption {
                font-size: 0.85rem;
                color: #6b7280;
            }
            @media (max-width: 640px) {
                div[data-testid="stHorizontalBlock"] {
                    flex-direction: column !important;
                }
                div[data-testid="stHorizontalBlock"] > div {
                    width: 100% !important;
                    min-width: 100% !important;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


def render_error_panel() -> None:
    error = st.session_state.get("last_error")
    detail = st.session_state.get("error_detail")
    if error:
        with st.container():
            st.error(error)
            if detail:
                with st.expander("Details"):
                    st.code(detail)


def render_capture() -> None:
    st.header("1. Capture")

    render_error_panel()

    col_cam, col_up = st.columns([1, 1])
    with col_cam:
        camera_file = st.camera_input("Capture plate")
    with col_up:
        upload_file = st.file_uploader(
            "Upload photo", type=["jpg", "jpeg", "png", "webp"]
        )

    camera_bytes = _read_uploaded(camera_file)
    upload_bytes = _read_uploaded(upload_file)
    # Prefer the camera image when both are present.
    image_bytes = camera_bytes or upload_bytes

    if image_bytes != st.session_state.get("image_bytes"):
        st.session_state["image_bytes"] = image_bytes
        st.session_state["image_hash"] = (
            _image_hash(image_bytes) if image_bytes else None
        )
        st.session_state["ocr_results"] = None
        st.session_state["selected_plate"] = None
        st.session_state["ocr_score"] = None
        st.session_state["ocr_region"] = None
        st.session_state["last_ocr_hash"] = None
        st.session_state["complaint"] = None
        st.session_state["last_error"] = None
        st.session_state["error_detail"] = None

    if image_bytes:
        st.image(image_bytes, caption="Selected photo", width="stretch")

    recognise_disabled = image_bytes is None
    if st.button(
        "Recognise plate",
        type="primary",
        disabled=recognise_disabled,
        width="stretch",
    ):
        if image_bytes:
            with st.spinner("Recognising plate…"):
                run_ocr(image_bytes)
            # Auto-advance immediately after recognition only when there is a
            # single clear candidate. Multiple candidates are shown in a picker.
            results = st.session_state.get("ocr_results")
            if results:
                strong = [r for r in results if r.score >= OCR_CANDIDATE_THRESHOLD]
                if len(strong) <= 1:
                    chosen = strong[0] if strong else results[0]
                    st.session_state["selected_plate"] = chosen.plate
                    st.session_state["ocr_score"] = chosen.score
                    st.session_state["ocr_region"] = chosen.region
                    go_confirm()

    results = st.session_state.get("ocr_results")
    if results is not None:
        if len(results) == 0:
            st.warning("No plate detected. You can enter it manually below.")
            _render_manual_entry()
        else:
            strong = [r for r in results if r.score >= OCR_CANDIDATE_THRESHOLD]
            if len(strong) > 1:
                _render_candidate_picker(strong)
            else:
                chosen = strong[0] if strong else results[0]
                _render_single_result_summary(chosen)
    else:
        # Always offer a manual path so the guard is never blocked.
        with st.expander("Enter plate manually instead"):
            _render_manual_entry()


def _render_manual_entry() -> None:
    manual = st.text_input(
        "Plate number",
        key="manual_plate",
        help="Type the plate exactly as it appears.",
    )
    if st.button("Continue with this plate", width="stretch"):
        st.session_state["selected_plate"] = manual
        st.session_state["ocr_score"] = None
        st.session_state["ocr_region"] = None
        go_confirm()


def _render_candidate_picker(candidates: list) -> None:
    st.info("Several plates were detected. Pick the one you want to use.")
    options = []
    for r in candidates:
        label = f"{format_vn(r.plate)} — confidence {int(r.score * 100)}%"
        options.append((label, r))

    selected_label = st.radio(
        "Detected plates",
        options=[opt[0] for opt in options],
        index=0,
    )
    selected_result = next(r for label, r in options if label == selected_label)

    if st.button("Use selected plate", type="primary", width="stretch"):
        st.session_state["selected_plate"] = selected_result.plate
        st.session_state["ocr_score"] = selected_result.score
        st.session_state["ocr_region"] = selected_result.region
        go_confirm()


def _render_single_result_summary(result) -> None:
    """Show a single OCR result on the capture screen with a manual fallback."""
    st.success(
        f"Plate detected: {format_vn(result.plate)} (confidence {int(result.score * 100)}%)"
    )
    if st.button("Continue to confirm", type="primary", width="stretch"):
        st.session_state["selected_plate"] = result.plate
        st.session_state["ocr_score"] = result.score
        st.session_state["ocr_region"] = result.region
        go_confirm()
    with st.expander("Enter a different plate manually"):
        _render_manual_entry()


def render_confirm() -> None:
    st.header("2. Confirm")

    selected = st.session_state.get("selected_plate") or ""
    score = st.session_state.get("ocr_score")

    st.caption("Edit the plate if the OCR read it wrong.")
    confirmed = st.text_input(
        "Plate number",
        key="confirmed_plate",
        help="Example format: 51F-123.45",
    )

    label, colour, icon = _confidence_band(score)
    _badge(label, colour, icon)

    if score is not None and score < OCR_CONFIDENCE_WARN:
        st.warning(
            "Low confidence — please check every character carefully before searching.",
            icon="⚠",
        )

    st.caption(f"Canonical: {canonical(confirmed) or '—'}")

    if st.button("Search owner", type="primary", width="stretch"):
        db = get_owner_db()
        if db is None:
            return
        searched = canonical(confirmed)
        owner = db.lookup(searched)
        go_result(owner, searched)

    if st.button("Back to photo", width="stretch"):
        st.session_state["step"] = "capture"
        st.rerun()


def render_result() -> None:
    st.header("3. Result")

    owner = st.session_state.get("owner")
    searched = st.session_state.get("searched_plate") or ""
    db = get_owner_db()

    if owner is not None:
        st.success(f"Found — Unit {owner.unit_no}")
        st.markdown(f"**Plate:** {format_vn(owner.plate)}")
        if st.button("Generate complaint", type="primary", width="stretch"):
            _generate_complaint(owner)
    else:
        st.error(f"Plate not found: {format_vn(searched)}")
        if db is not None:
            suggestions = db.suggestions(searched, limit=3)
            if suggestions:
                st.markdown("**Did you mean?**")
                for suggested_owner, sim in suggestions:
                    label = (
                        f"{format_vn(suggested_owner.plate)} — Unit {suggested_owner.unit_no} "
                        f"({int(sim * 100)}% match)"
                    )
                    if st.button(label, key=f"suggest_{suggested_owner.key}", width="stretch"):
                        go_result(suggested_owner, suggested_owner.key)

    complaint = st.session_state.get("complaint")
    if complaint:
        _render_complaint_draft(complaint)

    _render_filed_list()

    if st.button("Scan another plate", type="primary", width="stretch"):
        go_capture()


def _generate_complaint(owner: Owner) -> None:
    image_bytes = st.session_state.get("image_bytes")
    score = st.session_state.get("ocr_score")
    plate = st.session_state.get("searched_plate") or owner.plate
    complaint = build_complaint(
        plate=plate,
        unit_no=owner.unit_no,
        ocr_score=score,
        confirmed_by="operator",
        image_bytes=image_bytes,
    )
    st.session_state["complaint"] = complaint
    st.session_state["filed_complaints"] = [
        *st.session_state.get("filed_complaints", []),
        complaint,
    ]
    st.rerun()


def _render_complaint_draft(complaint: dict) -> None:
    with st.container(border=True):
        st.subheader("Complaint draft")

        col1, col2 = st.columns([2, 1])
        with col1:
            st.markdown(f"**Plate:** {complaint.get('plate') or 'N/A'}")
            st.markdown(f"**Unit:** {complaint.get('unit_no') or 'Unknown'}")
            st.markdown(f"**Time:** {complaint.get('created_at') or '—'}")
            score = complaint.get("ocr_score")
            label, colour, icon = _confidence_band(score)
            _badge(label, colour, icon)
        with col2:
            image_bytes = st.session_state.get("image_bytes")
            if image_bytes:
                st.image(image_bytes, width=120)
            else:
                st.caption("No photo")

        st.markdown("**Copy this text**")
        st.code(render_complaint_text(complaint), language="text")


def _render_filed_list() -> None:
    filed = st.session_state.get("filed_complaints", [])
    if not filed:
        return

    st.divider()
    st.markdown("**Filed this session** (clears when you refresh the page)")
    for item in filed[-5:]:
        plate = item.get("plate") or "—"
        unit = item.get("unit_no") or "Unknown"
        created = item.get("created_at") or "—"
        st.markdown(f"- {plate} · Unit {unit} · {created}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    st.set_page_config(
        page_title="Plate Complaint",
        page_icon="🚗",
        layout="centered",
        initial_sidebar_state="collapsed",
    )
    _apply_css()
    init_session_state()

    st.title("Plate Complaint")
    st.caption("Snap a plate photo, confirm the number, and look up the owner unit.")

    if not PLATERECOGNIZER_TOKEN and not PLATERECOGNIZER_TEST_MODE:
        st.error(
            "No Plate Recognizer token configured. Set `PLATERECOGNIZER_TOKEN` in a "
            "local `.env` file, or in the app's secrets when deployed on Streamlit "
            "Community Cloud."
        )
        return

    if not OWNERS_CSV.exists():
        st.warning(
            f"Owner database not found at `{OWNERS_CSV}`. "
            "Add a CSV with columns `plate` and `unit_no` to enable lookups."
        )

    step = st.session_state.get("step", "capture")
    if step == "capture":
        render_capture()
    elif step == "confirm":
        render_confirm()
    elif step == "result":
        render_result()
    else:
        # Defensive fallback.
        reset_flow()
        render_capture()


if __name__ == "__main__":
    main()
