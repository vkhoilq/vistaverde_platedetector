"""Local owner database backed by a small ``plate,unit_no`` CSV file."""

from __future__ import annotations

import csv
import sys
from dataclasses import dataclass
from pathlib import Path

from plate_normalize import canonical, distance, similarity

__all__ = ["Owner", "OwnerDB", "OwnerDBError"]

_REQUIRED_COLUMNS = ("plate", "unit_no")


class OwnerDBError(RuntimeError):
    """Raised when an existing owner CSV cannot be read or understood."""


@dataclass(frozen=True)
class Owner:
    plate: str
    unit_no: str
    key: str  # canonical form of plate


def _normalized_header(fieldnames: list[str] | None) -> dict[str, str]:
    """Map lower-cased, stripped header name -> actual header name in the file."""
    mapping: dict[str, str] = {}
    for name in fieldnames or []:
        if name is None:
            continue
        mapping.setdefault(name.strip().lower(), name)
    return mapping


class OwnerDB:
    """In-memory plate -> owner index that can hot-reload from disk."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._owners: dict[str, Owner] = {}
        self._mtime_ns: int | None = None
        self.reload()

    # ---------------------------------------------------------------- loading

    def reload(self) -> None:
        """Re-read the CSV from disk. A missing file means an empty database."""
        self._owners = {}
        self._mtime_ns = None

        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return
        except OSError as exc:  # exists but unreachable (permissions, ...)
            raise OwnerDBError(f"Could not stat owner database {self.path}: {exc}") from exc

        owners: dict[str, Owner] = {}
        try:
            with self.path.open(newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                header = _normalized_header(reader.fieldnames)
                missing = [column for column in _REQUIRED_COLUMNS if column not in header]
                if missing:
                    raise OwnerDBError(
                        f"Owner database {self.path} is missing column(s): "
                        + ", ".join(missing)
                    )
                plate_col = header["plate"]
                unit_col = header["unit_no"]
                for row in reader:
                    raw_plate = (row.get(plate_col) or "").strip()
                    unit_no = (row.get(unit_col) or "").strip()
                    if not raw_plate:
                        continue
                    key = canonical(raw_plate)
                    if not key:
                        continue
                    if key in owners:
                        print(
                            f"owner_db: duplicate plate key {key!r} for "
                            f"{raw_plate!r}; keeping {owners[key].plate!r} "
                            f"({owners[key].unit_no!r}), ignoring {unit_no!r}",
                            file=sys.stderr,
                        )
                        continue
                    owners[key] = Owner(plate=raw_plate, unit_no=unit_no, key=key)
        except OwnerDBError:
            raise
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise OwnerDBError(f"Could not read owner database {self.path}: {exc}") from exc

        self._owners = owners
        self._mtime_ns = stat.st_mtime_ns

    def maybe_reload(self) -> bool:
        """Reload only when the file's mtime changed. True when a reload happened."""
        try:
            current = self.path.stat().st_mtime_ns
        except FileNotFoundError:
            current = None
        except OSError:
            return False

        if current == self._mtime_ns:
            return False
        self.reload()
        return True

    # ---------------------------------------------------------------- queries

    def lookup(self, plate: str) -> Owner | None:
        """Exact canonical match only; never fuzzy."""
        return self._owners.get(canonical(plate))

    def suggestions(self, plate: str, limit: int = 3) -> list[tuple[Owner, float]]:
        """Near matches (confusion-aware distance <= 2), best first."""
        if limit <= 0:
            return []
        query = canonical(plate)
        scored: list[tuple[Owner, float, float]] = []
        for owner in self._owners.values():
            if distance(query, owner.key) <= 2:
                scored.append((owner, similarity(query, owner.key), distance(query, owner.key)))
        # Deterministic: score desc, then unit_no asc, then plate asc.
        scored.sort(key=lambda item: (-item[1], item[0].unit_no, item[0].plate))
        return [(owner, score) for owner, score, _dist in scored[:limit]]

    def __len__(self) -> int:
        return len(self._owners)
