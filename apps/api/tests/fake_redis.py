from __future__ import annotations

import asyncio
import time
from typing import Any


class _FakePipeline:
    def __init__(self, client: "FakeAsyncRedis"):
        self._client = client
        self._ops: list[tuple[str, tuple, dict]] = []

    def incr(self, name: str) -> "_FakePipeline":
        self._ops.append(("incr", (name,), {}))
        return self

    def expire(self, name: str, time: int, nx: bool = False) -> "_FakePipeline":
        self._ops.append(("expire", (name, time), {"nx": nx}))
        return self

    def set(self, name: str, value: str, ex: int | None = None) -> "_FakePipeline":
        self._ops.append(("set", (name, value), {"ex": ex}))
        return self

    def delete(self, *names: str) -> "_FakePipeline":
        self._ops.append(("delete", names, {}))
        return self

    async def execute(self) -> list[Any]:
        if self._client.fail_pipeline_execute:
            self._ops.clear()
            raise ConnectionError("Authentication state service unavailable")
        staged = list(self._ops)
        self._ops.clear()
        async with self._client._lock:
            results: list[Any] = []
            for op, args, kwargs in staged:
                if op == "incr":
                    results.append(self._client._incr_unlocked(*args))
                elif op == "expire":
                    results.append(self._client._expire_unlocked(*args, **kwargs))
                elif op == "set":
                    results.append(self._client._set_unlocked(*args, **kwargs))
                elif op == "delete":
                    results.append(self._client._delete_unlocked(*args))
                else:
                    raise RuntimeError(f"Unsupported fake pipeline op: {op}")
            return results

    async def __aenter__(self) -> "_FakePipeline":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeAsyncRedis:
    """Minimal async Redis stand-in for auth-state tests (no local Redis daemon)."""

    def __init__(self):
        self._values: dict[str, str] = {}
        self._expiry: dict[str, float] = {}
        self._closed = False
        self.fail_closed = False
        self.fail_pipeline_execute = False
        self._lock = asyncio.Lock()

    def _ensure_open(self) -> None:
        if self._closed or self.fail_closed:
            raise ConnectionError("Authentication state service unavailable")

    def _purge_if_expired(self, key: str) -> None:
        expires_at = self._expiry.get(key)
        if expires_at is not None and expires_at <= time.time():
            self._values.pop(key, None)
            self._expiry.pop(key, None)

    def _incr_unlocked(self, name: str) -> int:
        self._purge_if_expired(name)
        current = int(self._values.get(name, "0"))
        current += 1
        self._values[name] = str(current)
        return current

    def _expire_unlocked(self, name: str, time_seconds: int, nx: bool = False) -> bool:
        self._purge_if_expired(name)
        if name not in self._values:
            return False
        if nx and name in self._expiry:
            return False
        if time_seconds <= 0:
            self._values.pop(name, None)
            self._expiry.pop(name, None)
            return True
        self._expiry[name] = time.time() + int(time_seconds)
        return True

    def _set_unlocked(self, name: str, value: str, ex: int | None = None) -> bool:
        self._values[name] = str(value)
        if ex is None:
            self._expiry.pop(name, None)
        else:
            self._expiry[name] = time.time() + max(0, int(ex))
            if ex <= 0:
                self._values.pop(name, None)
                self._expiry.pop(name, None)
        return True

    def _delete_unlocked(self, *names: str) -> int:
        removed = 0
        for name in names:
            self._purge_if_expired(name)
            if name in self._values:
                self._values.pop(name, None)
                self._expiry.pop(name, None)
                removed += 1
        return removed

    async def ping(self) -> bool:
        self._ensure_open()
        return True

    async def get(self, name: str) -> str | None:
        self._ensure_open()
        async with self._lock:
            self._purge_if_expired(name)
            return self._values.get(name)

    async def set(self, name: str, value: str, ex: int | None = None) -> bool:
        self._ensure_open()
        async with self._lock:
            return self._set_unlocked(name, value, ex=ex)

    async def delete(self, *names: str) -> int:
        self._ensure_open()
        async with self._lock:
            return self._delete_unlocked(*names)

    async def incr(self, name: str) -> int:
        self._ensure_open()
        async with self._lock:
            return self._incr_unlocked(name)

    async def expire(self, name: str, time_seconds: int, nx: bool = False) -> bool:
        self._ensure_open()
        async with self._lock:
            return self._expire_unlocked(name, time_seconds, nx=nx)

    def pipeline(self, transaction: bool = True) -> _FakePipeline:
        return _FakePipeline(self)

    async def aclose(self) -> None:
        self._closed = True

    def force_expire(self, name: str) -> None:
        self._expiry[name] = time.time() - 1
        self._purge_if_expired(name)
