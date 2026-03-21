"""Unit tests for the in-memory work lock registry."""

import threading
import time

import pytest

from sotascope.services.work_lock import _WorkLockRegistry


@pytest.fixture()
def registry():
    return _WorkLockRegistry()


class TestAcquireRelease:
    def test_acquire_returns_true_for_unlocked_work(self, registry):
        assert registry.acquire(1, "task A") is True

    def test_acquire_returns_false_for_locked_work(self, registry):
        registry.acquire(1, "task A")
        assert registry.acquire(1, "task B") is False

    def test_release_allows_reacquire(self, registry):
        registry.acquire(1, "task A")
        registry.release(1)
        assert registry.acquire(1, "task B") is True

    def test_release_noop_on_unlocked_work(self, registry):
        registry.release(99)  # should not raise

    def test_different_work_ids_are_independent(self, registry):
        assert registry.acquire(1, "task A") is True
        assert registry.acquire(2, "task B") is True
        assert registry.acquire(1, "task C") is False  # still locked
        registry.release(1)
        assert registry.acquire(1, "task C") is True


class TestIsLocked:
    def test_unlocked_work_returns_false(self, registry):
        assert registry.is_locked(1) is False

    def test_locked_work_returns_true(self, registry):
        registry.acquire(1, "task")
        assert registry.is_locked(1) is True

    def test_released_work_returns_false(self, registry):
        registry.acquire(1, "task")
        registry.release(1)
        assert registry.is_locked(1) is False


class TestGetStatus:
    def test_returns_none_for_unlocked_work(self, registry):
        assert registry.get_status(1) is None

    def test_returns_status_for_locked_work(self, registry):
        registry.acquire(1, "Fetching citations")
        status = registry.get_status(1)
        assert status is not None
        assert status["locked"] is True
        assert status["task"] == "Fetching citations"

    def test_returns_none_after_release(self, registry):
        registry.acquire(1, "Fetching citations")
        registry.release(1)
        assert registry.get_status(1) is None


class TestGetAllLocked:
    def test_empty_when_no_locks(self, registry):
        assert registry.get_all_locked() == {}

    def test_returns_all_locked_works(self, registry):
        registry.acquire(1, "task A")
        registry.acquire(2, "task B")
        result = registry.get_all_locked()
        assert result == {1: "task A", 2: "task B"}

    def test_released_works_not_included(self, registry):
        registry.acquire(1, "task A")
        registry.acquire(2, "task B")
        registry.release(1)
        result = registry.get_all_locked()
        assert result == {2: "task B"}


class TestStaleness:
    def _age_lock(self, registry, work_id: int, age_seconds: float = 700) -> None:
        """Manually age a lock entry past the staleness threshold."""
        with registry._mu:
            task, _ = registry._locks[work_id]
            registry._locks[work_id] = (task, time.monotonic() - age_seconds)

    def test_stale_lock_treated_as_unlocked(self, registry):
        registry.acquire(1, "old task")
        self._age_lock(registry, 1)
        assert registry.is_locked(1) is False

    def test_acquire_succeeds_after_stale_lock(self, registry):
        registry.acquire(1, "old task")
        self._age_lock(registry, 1)
        assert registry.acquire(1, "new task") is True

    def test_get_status_returns_none_for_stale_lock(self, registry):
        registry.acquire(1, "old task")
        self._age_lock(registry, 1)
        assert registry.get_status(1) is None

    def test_get_all_locked_excludes_stale_entries(self, registry):
        registry.acquire(1, "stale task")
        registry.acquire(2, "fresh task")
        self._age_lock(registry, 1)
        result = registry.get_all_locked()
        assert 1 not in result
        assert 2 in result


class TestThreadSafety:
    def test_concurrent_acquire_only_one_wins(self, registry):
        """Only one thread should succeed in acquiring the same lock."""
        results = []

        def try_acquire():
            results.append(registry.acquire(42, "concurrent task"))

        threads = [threading.Thread(target=try_acquire) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert results.count(True) == 1
        assert results.count(False) == 9
