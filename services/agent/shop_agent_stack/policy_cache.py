"""Bounded, versioned cache of BM25 *index construction*, never policy answers.

The authoritative Java customer-visible catalog is fetched and its epoch is
checked on EVERY search before this cache is consulted. No user query, model
text, credential, staff/draft policy or permission result is cached. The exact
freshly returned content and metadata, epoch and local date bind a hit.
"""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from threading import Lock

from .knowledge import LexicalIndex

MAX_CACHED_CLAUSES = 2048
MAX_CACHED_BYTES = 2 * 1024 * 1024


class PublishedPolicyIndexCache:
    """One immutable published snapshot, with strictly bounded retained memory.

    The index is reused only if the entire freshly authorized snapshot matches.
    We intentionally do not cache Java pages, source verification or queries:
    published policy withdrawal must be visible immediately.
    """

    def __init__(self, *, max_clauses=MAX_CACHED_CLAUSES, max_bytes=MAX_CACHED_BYTES):
        if (type(max_clauses) is not int or not 1 <= max_clauses <= MAX_CACHED_CLAUSES
                or type(max_bytes) is not int or not 1024 <= max_bytes <= MAX_CACHED_BYTES):
            raise ValueError("invalid policy cache limits")
        self.max_clauses = max_clauses
        self.max_bytes = max_bytes
        self._lock = Lock()
        self._key = None
        self._index = None
        self._hits = 0
        self._builds = 0
        self._bypasses = 0

    def _key_for(self, epoch, documents, at):
        if len(documents) > self.max_clauses:
            return None
        digest = sha256()
        size = 0
        for doc in documents:
            # Use the ACTUAL text in addition to Java's content_hash. If a
            # producer accidentally reuses a content hash, no stale text wins.
            row = json.dumps((doc.page_content, doc.metadata), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
            size += len(row)
            if size > self.max_bytes:
                return None
            digest.update(len(row).to_bytes(8, "big"))
            digest.update(row)
        return epoch, at.isoformat(), len(documents), digest.hexdigest()

    def get(self, documents, *, epoch: int, at: date):
        """Return (index, cache_status) from current Java-authenticated rows.

        No cache result is served before the caller checks the Java epoch and
        full current catalog. A new/withdrawn clause changes the fingerprint
        even when the Java epoch were accidentally left unchanged.
        """
        if type(epoch) is not int or epoch < 0 or not isinstance(at, date):
            raise ValueError("invalid authoritative policy snapshot")
        documents = list(documents)
        key = self._key_for(epoch, documents, at)
        with self._lock:
            if key is not None and self._key == key and self._index is not None:
                self._hits += 1
                return self._index, "hit"
            # LexicalIndex performs publication/visibility/version checks.
            # Never publish a partial index or bypass a failed policy check.
            index = LexicalIndex(documents, at=at)
            if key is None or len(index.documents) != len(documents):
                self._key = self._index = None
                self._bypasses += 1
                return index, "bypass"
            self._key, self._index = key, index
            self._builds += 1
            return index, "miss"

    def statistics(self):
        """Safe diagnostic counters; no queries, policy bodies or IDs."""
        with self._lock:
            return {"hits": self._hits, "builds": self._builds,
                    "bypasses": self._bypasses,
                    "current_cached": self._index is not None}
