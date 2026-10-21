"""Phase 1 单元测试 —— 验证 Fail-Fast / Deadline / Output Guard / Model Tier Config。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parent.parent.parent / "4-引擎代码"
sys.path.insert(0, str(ENGINE_ROOT))

import pytest  # noqa: E402


class TestSettings:
    def test_dev_allows_mock(self):
        from drama_engine.core.settings import AppEnv, Settings
        s = Settings(app_env=AppEnv.DEV)
        assert s.allow_mock is True
        assert s.is_prod is False

    def test_prod_disallows_mock(self):
        from drama_engine.core.settings import AppEnv, Settings
        s = Settings(app_env=AppEnv.PROD)
        assert s.allow_mock is False
        assert s.is_prod is True

    def test_tier_models_have_defaults(self):
        from drama_engine.core.settings import get_settings
        s = get_settings()
        assert s.llm_fast_model != ""
        assert s.llm_balanced_model != ""
        assert s.llm_strong_model != ""
        assert s.llm_long_model != ""


class TestErrors:
    def test_all_codes_unique(self):
        from drama_engine.core.errors import EngineErrorCode
        vals = [c.value for c in EngineErrorCode]
        assert len(vals) == len(set(vals)), "error codes must be unique"

    def test_error_to_dict(self):
        from drama_engine.core.errors import EmptyOutputError
        e = EmptyOutputError("test message")
        d = e.to_dict()
        assert d["code"] == "EMPTY_OUTPUT"
        assert d["message"] == "test message"

    def test_classify_http_429(self):
        from drama_engine.core.errors import ModelRateLimitedError, classify_http_error
        e = classify_http_error(429, "too many requests")
        assert isinstance(e, ModelRateLimitedError)

    def test_classify_http_503(self):
        from drama_engine.core.errors import ModelUnavailableError, classify_http_error
        e = classify_http_error(503, "unavailable")
        assert isinstance(e, ModelUnavailableError)

    def test_classify_http_400_not_retryable(self):
        from drama_engine.core.errors import ModelInvalidOutputError, classify_http_error
        e = classify_http_error(400, "bad request")
        assert isinstance(e, ModelInvalidOutputError)


class TestDeadline:
    def test_remaining_decreases(self):
        from drama_engine.core.deadline import DeadlineContext
        d = DeadlineContext(1.0)
        assert d.remaining_seconds() > 0
        time.sleep(0.1)
        assert d.remaining_seconds() < 1.0

    def test_expired(self):
        from drama_engine.core.deadline import DeadlineContext
        d = DeadlineContext(0.01)
        time.sleep(0.02)
        assert d.expired() is True

    def test_ensure_time_raises(self):
        from drama_engine.core.deadline import DeadlineContext
        from drama_engine.core.errors import GenerationTimeoutError
        d = DeadlineContext(0.01)
        time.sleep(0.02)
        with pytest.raises(GenerationTimeoutError):
            d.ensure_time("test", minimum_required=999)

    def test_effective_timeout(self):
        from drama_engine.core.deadline import DeadlineContext
        d = DeadlineContext(10.0)
        assert d.effective_timeout(30.0) == 10.0  # capped by deadline
        assert d.effective_timeout(3.0) == 3.0   # under deadline

    def test_child_deadline(self):
        from drama_engine.core.deadline import DeadlineContext
        d = DeadlineContext(20.0)
        child = d.child("repair", 5.0)
        assert child.elapsed() < 0.1
        assert child.remaining_seconds() <= 5.0


class TestOutputGuard:
    def test_ok_content(self):
        from drama_engine.core.output_guard import validate_delivery
        r = validate_delivery("Hello World " * 10, "test", min_chars=20)
        assert r.ok()

    def test_null_content(self):
        from drama_engine.core.output_guard import validate_delivery
        r = validate_delivery(None, "test")
        assert not r.ok()
        assert any("null" in i.lower() for i in r.issues)

    def test_empty_content(self):
        from drama_engine.core.output_guard import validate_delivery
        r = validate_delivery("   ", "test")
        assert not r.ok()

    def test_too_short(self):
        from drama_engine.core.output_guard import validate_delivery
        r = validate_delivery("hi", "test", min_chars=100)
        assert not r.ok()

    def test_guard_ok(self):
        from drama_engine.core.output_guard import guard_delivery
        assert guard_delivery("hello world", "test", min_chars=1) == "hello world"

    def test_guard_raises(self):
        from drama_engine.core.output_guard import guard_delivery, EmptyOutputError
        import sys
        # EmptyOutputError is in drama_engine.core.errors
        from drama_engine.core.errors import EmptyOutputError as EOE
        with pytest.raises(EOE):
            guard_delivery(None, "test")


class TestJobRepo:
    def test_create_and_get(self):
        from drama_engine.core.job_repo import InMemoryJobRepository, JobStatus
        repo = InMemoryJobRepository()
        job = repo.create("req-1")
        assert job.status == JobStatus.PENDING
        assert job.job_id != ""

        found = repo.get(job.job_id)
        assert found is not None
        assert found.request_id == "req-1"

    def test_update_status(self):
        from drama_engine.core.job_repo import InMemoryJobRepository, JobStatus
        repo = InMemoryJobRepository()
        job = repo.create("req-2")
        repo.update(job.job_id, status=JobStatus.RUNNING)
        assert repo.get(job.job_id).status == JobStatus.RUNNING

        repo.update(job.job_id, status=JobStatus.SUCCEEDED, error_code="")
        assert repo.get(job.job_id).status == JobStatus.SUCCEEDED

    def test_list_recent(self):
        from drama_engine.core.job_repo import InMemoryJobRepository
        repo = InMemoryJobRepository()
        for i in range(5):
            repo.create(f"req-{i}")
        assert len(repo.list_recent(20)) == 5


class TestTelemetry:
    def test_trace_complete(self):
        from drama_engine.core.telemetry import new_trace, GenerationTelemetry
        tele = GenerationTelemetry(request_id="test-1")
        t = new_trace("topic_select", "deepseek-v4-flash", "FAST")
        t.latency_ms = 1500
        t.status = "ok"
        t.input_tokens = 100
        t.output_tokens = 50
        t.finished_at = t.started_at + 1.5
        tele.record(t)
        tele.stop()

        s = tele.summary()
        assert s["total_model_calls"] == 1
        assert s["total_input_tokens"] == 100
        assert s["error_count"] == 0
        assert s["slowest_node"]["node"] == "topic_select"


class TestModelTierMap:
    def test_default_tier_map(self):
        from drama_engine.llm.router import ModelTierMap, TIER_FAST, TIER_STRONG, TIER_LONG
        tm = ModelTierMap()
        assert tm.resolve(TIER_FAST) == "qwen/qwen3.6-flash"
        assert tm.resolve(TIER_STRONG) == "qwen/qwen3.7-max"
        assert tm.resolve(TIER_LONG) == "zhipu/glm-5.3"

    def test_legacy_compat(self):
        from drama_engine.llm.router import ModelTierMap
        tm = ModelTierMap()
        assert tm.resolve("cheap") == tm.resolve("FAST")
        assert tm.resolve("reasoning") == tm.resolve("STRONG")
        assert tm.resolve("strong") == tm.resolve("STRONG")

    def test_explicit_mapping(self):
        from drama_engine.llm.router import ModelTierMap, TIER_FAST
        tm = ModelTierMap({"FAST": "custom-fast-model"})
        assert tm.resolve(TIER_FAST) == "custom-fast-model"

    def test_resolve_spec_has_tier(self):
        from drama_engine.llm.router import ModelTierMap, resolve_spec, TIER_FAST
        tm = ModelTierMap()
        spec = resolve_spec("topic_select", tm)
        assert spec.tier == TIER_FAST
        assert spec.model != "mock"
