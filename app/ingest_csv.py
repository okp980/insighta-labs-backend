import csv
import io
from collections import defaultdict
from typing import BinaryIO

from sqlalchemy import select
from sqlmodel import Session, col

from .model.profiles import AgeGroup, Gender, Profile

CSV_HEADER = [
    "name",
    "gender",
    "gender_probability",
    "age",
    "age_group",
    "country_id",
    "country_name",
    "country_probability",
]


def _parse_float(val: str) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_int(val: str) -> int | None:
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def import_profiles_csv(*, session: Session, file_binary: BinaryIO, batch_size: int) -> dict:
    reasons: defaultdict[str, int] = defaultdict(int)
    total_rows = 0
    inserted = 0
    skipped = 0

    text_file = io.TextIOWrapper(file_binary, encoding="utf-8", newline="", errors="replace")
    reader = csv.reader(text_file)

    try:
        header_row = next(reader)
    except StopIteration:
        return {
            "status": "success",
            "total_rows": 0,
            "inserted": 0,
            "skipped": 0,
            "reasons": {},
        }

    if [h.strip() for h in header_row] != CSV_HEADER:
        text_file.detach()
        raise ValueError(f"CSV header must be exactly: {','.join(CSV_HEADER)} (got {header_row!r})")

    pending: list[Profile] = []
    pending_names: list[str] = []
    seen_in_file: set[str] = set()

    def flush_batch() -> None:
        nonlocal inserted, skipped, pending, pending_names
        if not pending:
            pending_names = []
            return

        db_hit = session.exec(
            select(Profile.name).where(col(Profile.name).in_(pending_names))
        ).all()
        existing_db = set(db_hit)

        batch_inserted = 0
        for profile, name in zip(pending, pending_names, strict=True):
            if name in existing_db:
                reasons["duplicate_name"] += 1
                skipped += 1
                continue
            session.add(profile)
            batch_inserted += 1
            existing_db.add(name)

        session.commit()
        inserted += batch_inserted
        pending = []
        pending_names = []

    for raw_vals in reader:
        total_rows += 1
        if any("\ufffd" in (c or "") for c in raw_vals):
            reasons["invalid_encoding"] += 1
            skipped += 1
            continue
        if len(raw_vals) != len(CSV_HEADER):
            reasons["malformed_row"] += 1
            skipped += 1
            continue

        vals = [v.strip() for v in raw_vals]
        if any(v == "" for v in vals):
            reasons["missing_fields"] += 1
            skipped += 1
            continue

        (
            name,
            gender_raw,
            gp_raw,
            age_raw,
            age_group_raw,
            country_id,
            country_name,
            cp_raw,
        ) = vals

        if name in seen_in_file:
            reasons["duplicate_name"] += 1
            skipped += 1
            continue

        gender_l = gender_raw.lower()
        if gender_l not in (Gender.male.value, Gender.female.value):
            reasons["invalid_gender"] += 1
            skipped += 1
            continue

        age = _parse_int(age_raw)
        if age is None or age < 0:
            reasons["invalid_age"] += 1
            skipped += 1
            continue

        agl = age_group_raw.lower()
        if agl not in (
            AgeGroup.child.value,
            AgeGroup.teenager.value,
            AgeGroup.adult.value,
            AgeGroup.senior.value,
        ):
            reasons["invalid_age_group"] += 1
            skipped += 1
            continue

        gp = _parse_float(gp_raw)
        cp = _parse_float(cp_raw)
        if gp is None or cp is None:
            reasons["invalid_probability"] += 1
            skipped += 1
            continue
        if gp < 0 or gp > 1 or cp < 0 or cp > 1:
            reasons["invalid_probability"] += 1
            skipped += 1
            continue

        profile = Profile(
            name=name,
            gender=Gender(gender_l),
            gender_probability=gp,
            age=age,
            age_group=AgeGroup(agl),
            country_id=country_id.upper(),
            country_name=country_name,
            country_probability=cp,
        )
        seen_in_file.add(name)
        pending.append(profile)
        pending_names.append(name)

        if len(pending) >= batch_size:
            flush_batch()

    flush_batch()
    text_file.detach()

    reasons_out = {k: v for k, v in reasons.items() if v > 0}
    return {
        "status": "success",
        "total_rows": total_rows,
        "inserted": inserted,
        "skipped": skipped,
        "reasons": reasons_out,
    }
