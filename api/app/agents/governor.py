"""Concurrency governor for LLM calls.

One local Ollama model serves extraction, authenticity, explanations and every
narration. Before the agent layer, those calls simply piled up behind each
other and the queue wait silently ate each caller's timeout (a five-document
review queued five authenticity calls behind a 60 s timeout). The gate fixes
both problems:

  * a per-provider concurrency limit (Ollama = OLLAMA_NUM_PARALLEL, default 1);
  * priorities — interactive upload-path work (extraction) is served before
    background narration (credit memo), whatever the arrival order;
  * the caller's timeout starts AFTER the slot is acquired; queue wait has its
    own cap (max_wait_s) after which the caller degrades gracefully.
"""
from __future__ import annotations

import heapq
import itertools
import threading
import time
from contextlib import contextmanager

from ..config import settings


class QueueTimeout(RuntimeError):
    """Waited longer than max_wait_s for an LLM slot."""


class LLMGate:
    def __init__(self):
        self._cv = threading.Condition()
        self._active: dict[str, int] = {}
        self._waiting: dict[str, list] = {}
        self._seq = itertools.count()
        self.stats = {"acquired": 0, "timeouts": 0, "max_wait_ms": 0.0}

    def limit(self, provider: str) -> int:
        return {"ollama": max(1, settings.ollama_parallel),
                "anthropic": max(1, settings.anthropic_parallel)}.get(provider, 64)

    def acquire(self, provider: str, priority: int = 3, max_wait_s: float = 120.0) -> float:
        entry = (priority, next(self._seq))
        t0 = time.perf_counter()
        deadline = t0 + max_wait_s
        with self._cv:
            q = self._waiting.setdefault(provider, [])
            heapq.heappush(q, entry)
            try:
                while True:
                    if q[0] == entry and self._active.get(provider, 0) < self.limit(provider):
                        heapq.heappop(q)
                        self._active[provider] = self._active.get(provider, 0) + 1
                        waited = (time.perf_counter() - t0) * 1000
                        self.stats["acquired"] += 1
                        self.stats["max_wait_ms"] = max(self.stats["max_wait_ms"], waited)
                        self._cv.notify_all()
                        return waited
                    remaining = deadline - time.perf_counter()
                    if remaining <= 0:
                        q.remove(entry)
                        heapq.heapify(q)
                        self.stats["timeouts"] += 1
                        self._cv.notify_all()
                        raise QueueTimeout(
                            f"waited {max_wait_s:.0f}s for a free {provider} slot")
                    self._cv.wait(timeout=min(remaining, 1.0))
            except BaseException:
                if entry in q:
                    q.remove(entry)
                    heapq.heapify(q)
                raise

    def release(self, provider: str):
        with self._cv:
            self._active[provider] = max(0, self._active.get(provider, 0) - 1)
            self._cv.notify_all()

    @contextmanager
    def slot(self, provider: str, priority: int = 3, max_wait_s: float = 120.0):
        waited = self.acquire(provider, priority, max_wait_s)
        try:
            yield waited
        finally:
            self.release(provider)

    def snapshot(self) -> dict:
        with self._cv:
            return {"active": dict(self._active),
                    "waiting": {p: len(q) for p, q in self._waiting.items()},
                    **self.stats}


GATE = LLMGate()

# CPU-heavy tool work that should not oversubscribe the machine when several
# documents are analysed at once (tampering cascade, OCR passes).
TAMPER_CPU = threading.BoundedSemaphore(2)
