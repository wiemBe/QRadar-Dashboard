"""Deterministic alert fingerprints, computed at the application layer.

The fingerprint is the identity of an alertable *condition* — not of a single
detection. Two detections of "log source X has VOLUME_DROP" must produce the
same fingerprint so the second updates the open alert instead of opening a new
one.

Layering:
  * This module is the primary dedup mechanism: the service looks up the open
    alert by fingerprint before deciding to open vs. update.
  * The partial unique index on ``alert(dedup_key) WHERE status <> 'RESOLVED'``
    (see app/models/alert.py) is the *final* concurrency safeguard, catching the
    race where two workers compute the same fingerprint simultaneously. The DB
    constraint should essentially never fire; if it does, the service treats the
    IntegrityError as "someone else opened it" and reloads.

Determinism rules:
  * Only stable identifiers go into the hash — never timestamps, counts, or
    observed values, which change every interval.
  * Component order is fixed and values are normalised (lower-cased, stripped)
    so the same logical condition always hashes identically.
"""

from __future__ import annotations

import hashlib
import uuid


def _normalise(value: object) -> str:
    return str(value).strip().lower()


def compute_fingerprint(*, source_type: str, source_id: str | uuid.UUID, condition: str,
                        subtype: str | None = None) -> str:
    """Stable fingerprint for an alertable condition.

    Args:
        source_type: e.g. "log_source", "scheduled_search".
        source_id:   the stable id of that object.
        condition:   the condition family, e.g. "anomaly", "search_threshold".
        subtype:     optional discriminator, e.g. the anomaly type "VOLUME_DROP".
    """
    parts = [_normalise(source_type), _normalise(source_id), _normalise(condition)]
    if subtype is not None:
        parts.append(_normalise(subtype))
    joined = "|".join(parts)
    digest = hashlib.sha256(joined.encode()).hexdigest()[:32]
    return f"{condition}:{digest}"


def anomaly_fingerprint(log_source_id: str | uuid.UUID, anomaly_type: str) -> str:
    return compute_fingerprint(
        source_type="log_source",
        source_id=log_source_id,
        condition="anomaly",
        subtype=anomaly_type,
    )


def search_threshold_fingerprint(search_id: str | uuid.UUID) -> str:
    return compute_fingerprint(
        source_type="scheduled_search",
        source_id=search_id,
        condition="search_threshold",
    )


def search_failure_fingerprint(search_id: str | uuid.UUID) -> str:
    """Identity of "this scheduled search keeps failing to run".

    Deliberately distinct from the threshold condition: a search that errors and
    a search whose result crossed its threshold are different problems with
    different owners, and must not deduplicate into one another.
    """
    return compute_fingerprint(
        source_type="scheduled_search",
        source_id=search_id,
        condition="search_failure",
    )
