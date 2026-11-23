"""Representative hit frames read from W2's private COS export.

W2 writes one 960px JPEG per externally visible consensus record to
``<prefix>/frames/<task_id>/<HH-MM-SS>.jpg`` (the record's first hit timestamp) and a
``<prefix>/frames_manifest.json`` mapping ``task_id|program_name`` to the object key.

* The manifest is kept in memory and re-checked every 10 minutes with its ETag, always in
  a background thread; requests never wait for it.
* A record missing from the manifest falls back to the first-timestamp key.
* Image bytes are cached on local disk under a size cap; misses are remembered briefly.
* Every COS call has short timeouts. Any failure raises ``FrameUnavailable``, which the
  frame endpoint turns into 404 and the report into 「暂无截图」, never a 500.

Bytes go straight to ``FrameImage`` (blur or watermark). No COS URL leaves the server.
"""

import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import httpx

from nadou.frames import FrameUnavailable
from nadou.normalize import ProgramNormalizer

log = logging.getLogger(__name__)

_TIMESTAMP = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})")
_TASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class FrameTarget:
    """What locates one record's frame: its task, title and hit timestamps."""

    record_id: str
    task_id: str
    program_name: str
    program_name_raw: str
    timestamps: tuple[str, ...]

    @staticmethod
    def of(record) -> "FrameTarget":
        return FrameTarget(
            record_id=record.id,
            task_id=record.task_id or "",
            program_name=record.program_name or "",
            program_name_raw=record.program_name_raw or "",
            timestamps=tuple(str(t) for t in (record.timestamps or [])),
        )


class CosResponse:
    def __init__(self, status: int, body: bytes = b"", etag: str = "") -> None:
        self.status = status
        self.body = body
        self.etag = etag


class CosObjectClient:
    """GET one private object with a COS v5 signature over httpx. Short timeouts."""

    SIGN_TTL = 600

    def __init__(
        self,
        secret_id: str,
        secret_key: str,
        bucket: str,
        region: str,
        *,
        timeout: httpx.Timeout | None = None,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._id = secret_id
        self._key = secret_key
        self._host = f"{bucket}.cos.{region}.myqcloud.com"
        self._now = now
        self._http = httpx.Client(
            timeout=timeout or httpx.Timeout(4.0, connect=2.0),
            transport=transport,
            follow_redirects=False,
        )

    def authorization(self, method: str, path: str) -> str:
        start = int(self._now()) - 60
        key_time = f"{start};{start + self.SIGN_TTL}"
        sign_key = hmac.new(self._key.encode(), key_time.encode(), hashlib.sha1).hexdigest()
        headers = f"host={quote(self._host, safe='')}"
        http_string = f"{method.lower()}\n{path}\n\n{headers}\n"
        digest = hashlib.sha1(http_string.encode()).hexdigest()
        to_sign = f"sha1\n{key_time}\n{digest}\n"
        signature = hmac.new(sign_key.encode(), to_sign.encode(), hashlib.sha1).hexdigest()
        return (
            f"q-sign-algorithm=sha1&q-ak={self._id}&q-sign-time={key_time}"
            f"&q-key-time={key_time}&q-header-list=host&q-url-param-list="
            f"&q-signature={signature}"
        )

    def get(self, key: str, *, if_none_match: str = "") -> CosResponse:
        path = "/" + key.lstrip("/")
        headers = {"Host": self._host, "Authorization": self.authorization("get", path)}
        if if_none_match:
            headers["If-None-Match"] = if_none_match
        response = self._http.get(f"https://{self._host}{quote(path)}", headers=headers)
        return CosResponse(
            response.status_code,
            response.content if response.status_code == 200 else b"",
            response.headers.get("etag", ""),
        )


class FrameManifest:
    """``task_id|program_name`` → object key, refreshed when its ETag changes."""

    def __init__(
        self,
        client,
        key: str,
        *,
        refresh_seconds: float = 600,
        retry_seconds: float = 60,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._key = key
        self._refresh = refresh_seconds
        self._retry = retry_seconds
        self._now = now
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()
        self._etag = ""
        self._entries: dict[tuple[str, str], str] = {}
        self._loaded = False
        self._refreshing = False
        self._next_check = 0.0

    @staticmethod
    def parse(raw: bytes) -> dict[tuple[str, str], str]:
        data = json.loads(raw)
        pairs: list[tuple[str, str, str]] = []
        done = data.get("done") if isinstance(data, dict) else None
        if isinstance(done, dict):
            for joined, path in done.items():
                task_id, sep, name = str(joined).partition("|")
                if sep and isinstance(path, str):
                    pairs.append((task_id, name, path))
        entries = data.get("entries") if isinstance(data, dict) else data
        if isinstance(entries, list):
            for entry in entries:
                if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                    pairs.append(
                        (
                            str(entry.get("task_id", "")),
                            str(entry.get("program_name", "")),
                            entry["path"],
                        )
                    )
        mapping: dict[tuple[str, str], str] = {}
        for task_id, name, path in pairs:
            if not task_id or not name or not path:
                continue
            mapping.setdefault((task_id, name), path)
            mapping.setdefault((task_id, ProgramNormalizer.canonical(name)), path)
        return mapping

    def lookup(self, target: FrameTarget) -> str | None:
        entries = self.entries()
        for name in (target.program_name_raw, target.program_name):
            if name:
                found = entries.get((target.task_id, name))
                if found:
                    return found
        return None

    def entries(self) -> dict[tuple[str, str], str]:
        """The current mapping; never waits on COS. The manifest is large (12 MB for the
        full export), so loads and ETag checks run in a background thread. Until the
        first load finishes, records use the first-timestamp key, which is the same key
        W2 writes."""
        with self._lock:
            if self._now() >= self._next_check and not self._refreshing:
                self._refreshing = True
                self._next_check = self._now() + self._retry
                threading.Thread(target=self._background, daemon=True).start()
            return self._entries

    def warm(self) -> None:
        """Start the first load in the background (called when the app starts)."""
        self.entries()

    def refresh_now(self) -> None:
        with self._load_lock:
            self._reload()

    def _background(self) -> None:
        try:
            with self._load_lock:
                self._reload()
        finally:
            with self._lock:
                self._refreshing = False

    def _reload(self) -> None:
        try:
            response = self._client.get(self._key, if_none_match=self._etag)
            if response.status == 304:
                with self._lock:
                    self._next_check = self._now() + self._refresh
                return
            if response.status != 200:
                raise FrameUnavailable(f"manifest status {response.status}")
            entries = self.parse(response.body)
            with self._lock:
                self._entries = entries
                self._etag = response.etag
                self._loaded = True
                self._next_check = self._now() + self._refresh
        except Exception as exc:  # keep serving the last good manifest
            log.warning("frame manifest refresh failed: %s", type(exc).__name__)
            with self._lock:
                self._next_check = self._now() + self._retry


class DiskFrameCache:
    """Object bytes on local disk, keyed by a hash of the object key, under a size cap."""

    def __init__(self, directory: str | Path, max_bytes: int) -> None:
        self._dir = Path(directory)
        self._max = max(0, max_bytes)
        self._lock = threading.Lock()
        self._size: int | None = None

    def _path(self, key: str) -> Path:
        return self._dir / (hashlib.sha256(key.encode()).hexdigest() + ".jpg")

    def get(self, key: str) -> bytes | None:
        if self._max == 0:
            return None
        path = self._path(key)
        try:
            data = path.read_bytes()
        except OSError:
            return None
        try:
            os.utime(path)
        except OSError:
            pass
        return data or None

    def put(self, key: str, data: bytes) -> None:
        if self._max == 0 or not data or len(data) > self._max:
            return
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            path = self._path(key)
            temp = path.with_suffix(f".{threading.get_ident()}.tmp")
            temp.write_bytes(data)
            os.replace(temp, path)
        except OSError:
            return
        with self._lock:
            if self._size is None:
                self._size = self._scan_size()
            else:
                self._size += len(data)
            if self._size > self._max:
                self._evict()

    def _files(self) -> list[Path]:
        try:
            return [p for p in self._dir.iterdir() if p.suffix == ".jpg"]
        except OSError:
            return []

    def _scan_size(self) -> int:
        total = 0
        for path in self._files():
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def _evict(self) -> None:
        stats = []
        for path in self._files():
            try:
                st = path.stat()
            except OSError:
                continue
            stats.append((st.st_mtime, st.st_size, path))
        stats.sort()
        total = sum(size for _m, size, _p in stats)
        target = int(self._max * 0.9)
        for _mtime, size, path in stats:
            if total <= target:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass
        self._size = total


class CosFrameSource:
    """DerivedFrameSource over W2's COS frame export. Every variant is the same 960px JPEG."""

    PARALLEL = 16
    MISS_SECONDS = 600
    # Keep the report response well inside the mini program's download timeout.
    PREFETCH_BUDGET = 8.0

    def __init__(
        self,
        client,
        prefix: str,
        cache: DiskFrameCache,
        *,
        lookup: Callable[[str], FrameTarget | None] | None = None,
        manifest: FrameManifest | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._prefix = prefix.strip("/")
        self._cache = cache
        self._lookup = lookup
        self._manifest = manifest or FrameManifest(client, self._key("frames_manifest.json"))
        self._now = now
        self._misses: dict[str, float] = {}
        self._lock = threading.Lock()

    @property
    def manifest(self) -> FrameManifest:
        return self._manifest

    def _key(self, tail: str) -> str:
        return f"{self._prefix}/{tail}" if self._prefix else tail

    @staticmethod
    def fallback_tail(target: FrameTarget) -> str | None:
        if not _TASK_ID.match(target.task_id) or not target.timestamps:
            return None
        match = _TIMESTAMP.match(target.timestamps[0].strip())
        if match is None:
            return None
        hh, mm, ss = match.groups()
        return f"frames/{target.task_id}/{int(hh):02d}-{mm}-{ss}.jpg"

    def keys_for(self, target: FrameTarget) -> list[str]:
        keys: list[str] = []
        listed = self._manifest.lookup(target)
        if listed:
            listed = listed.lstrip("/")
            # Manifest paths are full object keys that already start with the prefix.
            if self._prefix and not listed.startswith(self._prefix + "/"):
                listed = self._key(listed)
            keys.append(listed)
        tail = self.fallback_tail(target)
        if tail and self._key(tail) not in keys:
            keys.append(self._key(tail))
        return keys

    def fetch_target(self, target: FrameTarget) -> bytes:
        for key in self.keys_for(target):
            data = self._object(key)
            if data:
                return data
        raise FrameUnavailable(target.record_id)

    def fetch_record(self, record) -> bytes:
        return self.fetch_target(FrameTarget.of(record))

    def fetch_representative(self, variant: str, record_id: str, user_id: str | None) -> bytes:
        target = self._lookup(record_id) if self._lookup is not None else None
        if target is None:
            raise FrameUnavailable(record_id)
        return self.fetch_target(target)

    def prefetch(self, records: Iterable, budget_seconds: float | None = None) -> dict[str, bytes]:
        """Fetch many records' frames in parallel (capped) within a time budget.

        Frames that arrive late are still written to the disk cache in the background, so
        the next report has them; this report shows 「暂无截图」 for them."""
        targets = {t.record_id: t for t in (FrameTarget.of(r) for r in records)}
        if not targets:
            return {}
        budget = self.PREFETCH_BUDGET if budget_seconds is None else budget_seconds

        def one(target: FrameTarget) -> tuple[str, bytes | None]:
            try:
                return target.record_id, self.fetch_target(target)
            except FrameUnavailable:
                return target.record_id, None

        pool = ThreadPoolExecutor(max_workers=min(self.PARALLEL, len(targets)))
        futures = [pool.submit(one, target) for target in targets.values()]
        pool.shutdown(wait=False)
        found: dict[str, bytes] = {}
        try:
            for future in as_completed(futures, timeout=budget):
                record_id, data = future.result()
                if data:
                    found[record_id] = data
        except FuturesTimeout:
            log.warning("frame prefetch budget reached: %d of %d", len(found), len(targets))
        return found

    def _object(self, key: str) -> bytes | None:
        cached = self._cache.get(key)
        if cached:
            return cached
        with self._lock:
            until = self._misses.get(key)
            if until is not None and until > self._now():
                return None
        response = None
        for _attempt in range(2):  # one retry on a transport error or timeout
            try:
                response = self._client.get(key)
                break
            except Exception as exc:
                log.warning("frame fetch failed: %s", type(exc).__name__)
        if response is None:
            return None
        if response.status != 200 or not response.body:
            if response.status in (403, 404):
                with self._lock:
                    if len(self._misses) > 50000:
                        self._misses.clear()
                    self._misses[key] = self._now() + self.MISS_SECONDS
            return None
        self._cache.put(key, response.body)
        return response.body

    @staticmethod
    def from_settings(settings, database) -> "CosFrameSource | None":
        """Build the source from NADOU_FRAMES_COS_* settings, or None when unset."""
        if not settings.frames_configured:
            return None
        client = CosObjectClient(
            settings.frames_cos_secret_id,
            settings.frames_cos_secret_key.get_secret_value(),
            settings.frames_cos_bucket,
            settings.frames_cos_region,
        )
        cache = DiskFrameCache(
            settings.frames_cache_dir, settings.frames_cache_max_mb * 1024 * 1024
        )
        frames = CosFrameSource(
            client, settings.frames_cos_prefix, cache, lookup=RecordLookup(database)
        )
        frames.manifest.warm()
        return frames


class RecordLookup:
    """Record id → FrameTarget through a short session of its own."""

    def __init__(self, database) -> None:
        self._database = database

    def __call__(self, record_id: str) -> FrameTarget | None:
        from nadou.models import IndexRecord

        session = self._database.session()
        try:
            record = session.get(IndexRecord, record_id)
            return FrameTarget.of(record) if record is not None else None
        finally:
            session.close()
