"""Bounded, permission-independent *public* policy catalog snapshot.

Every get() is preceded and followed by an authenticated Java checkpoint.
Never cache customer entitlements, credentials, prompts, query results, or
anything other than the PUBLISHED/CUSTOMER clause fields from Java.
"""
from __future__ import annotations

import json
from threading import Lock

MAX_CLAUSES = 2048
MAX_BYTES = 2 * 1024 * 1024
FIELDS = frozenset({"clause_cursor", "policy_id", "clause_no", "content",
                    "content_hash", "title", "version", "family_id"})


def checkpoint_identity(stamp):
    if type(stamp) is not dict or set(stamp) != {
        "epoch", "serverTimeUs", "nextTransitionUs"
    }:
        raise ValueError("untrusted authoritative checkpoint")
    epoch, now, boundary = stamp["epoch"], stamp["serverTimeUs"], stamp["nextTransitionUs"]
    if type(epoch) is not int or epoch < 0 or type(now) is not int or now <= 0:
        raise ValueError("invalid authoritative epoch/time")
    if boundary is not None and (type(boundary) is not int or boundary <= now):
        raise ValueError("stale or malformed policy boundary")
    # This value has already been granted to a customer, but only Java owns
    # which policy rows are returned. No local trust in epoch alone.
    return epoch, boundary


def same_valid_snapshot(first, last):
    before = checkpoint_identity(first)
    after = checkpoint_identity(last)
    # The *database* clock must not cross a future effectiveness boundary.
    return before == after and last["serverTimeUs"] >= first["serverTimeUs"]


def canonical_public_rows(rows):
    if type(rows) is not list or len(rows) > MAX_CLAUSES:
        return None
    for row in rows:
        if type(row) is not dict or set(row) != FIELDS:
            raise ValueError("unexpected private or malformed policy fields")
        for field in ("clause_cursor", "policy_id", "clause_no", "version", "family_id"):
            if type(row[field]) is not int or row[field] <= 0:
                raise ValueError("invalid policy identity")
        for field in ("content", "content_hash", "title"):
            if type(row[field]) is not str or len(row[field]) > 30000:
                raise ValueError("invalid public clause content")
    data = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    if len(data) > MAX_BYTES:
        return None
    return data


class PublishedCatalogSnapshot:
    """One catalog generation in memory; reader receives defensive copies."""

    def __init__(self):
        self._lock = Lock()
        self._key = None
        self._rows = None
        self._hits = 0
        self._misses = 0
        self._bypasses = 0

    def read(self, stamp):
        key = checkpoint_identity(stamp)
        with self._lock:
            if self._key == key and self._rows is not None:
                self._hits += 1
                return json.loads(self._rows)
            self._misses += 1
        return None

    def publish(self, initial, final, rows):
        if not same_valid_snapshot(initial, final):
            raise ValueError("policy catalog changed during query")
        blob = canonical_public_rows(rows)
        with self._lock:
            if blob is None:
                self._key, self._rows = None, None
                self._bypasses += 1
                return False
            self._key = checkpoint_identity(final)
            self._rows = blob
            return True

    def clear(self):
        with self._lock:
            self._key = self._rows = None

    def metrics(self):
        with self._lock:
            return {"hits": self._hits, "misses": self._misses,
                    "bypasses": self._bypasses,
                    "stored": self._rows is not None}
