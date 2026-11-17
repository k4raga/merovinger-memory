"""Fresh external history only; no kernel, DB, Qt, model or network."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest
from agent_native_memory import (
    ConversationMemory, MemoryError, MemoryLimits, ContextBudget, CountEvidence,
)
from agent_native_memory.conversation import DEFAULT_MODEL, SERIALIZER_DIGEST


def populated(limits=None):
    memory = ConversationMemory(project_id="tests", conversation_id="chat", limits=limits)
    for i in range(10):
        group = memory.begin_turn("User " + str(i) + " " + "x" * 150, event_id=f"u{i}")
        memory.reply(group, "Answer " + str(i) + " " + "y" * 150, event_id=f"a{i}")
        memory.close_turn(group)
    memory.begin_turn("protected latest question", event_id="latest")
    return memory


def request(memory):
    prepared = memory.prepare(byte_limit=2500)
    assert prepared.status == "needs_compaction"
    assert prepared.byte_count <= 2500
    assert prepared.demand is not None
    return prepared


def test_empty_and_explicit_idempotent_text_groups():
    memory = ConversationMemory()
    assert memory.snapshot().window is None
    assert memory.prepare(byte_limit=256).status == "empty"
    assert memory.prepare(byte_limit=1).status == "cannot_fit"
    group = memory.user("Hello", event_id="u1")
    revision = memory.snapshot().revision
    assert memory.begin_turn("Hello", event_id="u1") == group
    assert memory.snapshot().revision == revision
    with pytest.raises(MemoryError):
        memory.begin_turn("Different", event_id="u1")
    memory.reply(group, "Hi", event_id="a1")
    assert memory.snapshot().history[0].state == "open"
    memory.close_turn(group)
    revision = memory.snapshot().revision
    memory.reply(group, "Hi", event_id="a1")
    assert memory.snapshot().revision == revision
    with pytest.raises(MemoryError):
        memory.reply(group, "More", event_id="a2")
    assert memory.prepare(byte_limit=2000).status == "ready"


def test_interleaved_open_groups_and_mandatory_overflow():
    memory = ConversationMemory()
    old = memory.begin_turn("old pending", event_id="u1")
    memory.begin_turn("latest", event_id="u2")
    memory.reply(old, "late response " + "z" * 500, event_id="a1")
    snap = memory.snapshot()
    assert snap.window.head_sequence == 3
    assert len(memory.prepare(byte_limit=2000).protected_source_keys) == 3
    tiny = memory.prepare(byte_limit=256)
    assert tiny.status == "cannot_fit"
    assert tiny.selected_group_ids == ("u1", "u2")
    assert ConversationMemory.from_state(memory.export_state()).snapshot() == snap


def test_bytes_prefix_selection_and_no_compaction_fallback():
    memory = populated()
    prepared = request(memory)
    assert "latest" not in prepared.selected_group_ids
    assert any(k.event_id == "latest" for k in prepared.protected_source_keys)
    # Adjacent eligible prefix is too large for this exact serializer/budget.
    last = prepared.demand.key.prefix_end
    bigger = memory.prepare(byte_limit=4000)
    assert bigger.demand.key.prefix_end > last
    fallback = memory.prepare(byte_limit=2500, allow_compaction=False)
    assert fallback.status == "ready"
    assert fallback.byte_count <= 2500 and b"protected latest question" in fallback.context
    assert memory.snapshot().attempts == ()
    with pytest.raises(MemoryError):
        memory.prepare(byte_limit=True)
    with pytest.raises(MemoryError):
        memory.prepare(byte_limit=2500, allow_compaction=1)


def test_explicit_attempt_summary_duplicate_collision_and_no_history_deletion():
    memory = populated()
    original = memory.snapshot().history
    prepared = request(memory)
    job = memory.begin_compaction(prepared)
    assert memory.snapshot().attempts[0].outcome == "inflight"
    assert job.input == prepared.context
    assert memory.prepare(byte_limit=2500).demand is None
    assert memory.complete_compaction(job.job_id, "Earlier exchanges.", expected_revision=job.revision) == "applied"
    accepted = memory.snapshot()
    assert accepted.history == original and accepted.summary is not None
    assert memory.complete_compaction(job.job_id, "Earlier exchanges.", expected_revision=0) == "duplicate"
    assert memory.complete_compaction(job.job_id, "Other text.", expected_revision=0) == "collision"
    assert memory.snapshot() == accepted
    restored = ConversationMemory.from_state(memory.export_state())
    assert restored.snapshot() == accepted
    visible = restored.prepare(byte_limit=2500, allow_compaction=False)
    assert visible.status == "ready" and b"Earlier exchanges." in visible.context


def test_unknown_survives_restore_and_never_retries_implicitly():
    memory = populated()
    job = memory.begin_compaction(request(memory))
    memory.mark_unknown(job.job_id)
    checkpoint = memory.export_state()
    restored = ConversationMemory.from_state(checkpoint)
    assert restored.export_state() == checkpoint
    assert restored.snapshot().attempts[0].outcome == "unknown"
    assert restored.prepare(byte_limit=2500).demand is None
    restored.begin_turn("A newer user", event_id="new")
    assert restored.prepare(byte_limit=2500).demand is None
    restored.fail_compaction(job.job_id)
    assert restored.snapshot().attempts[0].outcome == "failed"
    assert restored.snapshot().history[:len(memory.snapshot().history)] == memory.snapshot().history


def test_stale_revision_and_policy_change_do_not_apply():
    memory = populated()
    prepared = request(memory)
    memory.begin_turn("new", event_id="other")
    with pytest.raises(MemoryError, match="stale"):
        memory.begin_compaction(prepared)
    job = memory.begin_compaction(request(memory))
    memory.set_policy("b" * 64)
    assert memory.complete_compaction(job.job_id, "Old policy.", expected_revision=job.revision) == "stale"
    assert memory.snapshot().summary is None
    assert memory.complete_compaction(job.job_id, "Old policy.",
                                     expected_revision=memory.snapshot().revision) == "stale"
    assert memory.snapshot().attempts[-1].outcome == "stale"
    assert ConversationMemory.from_state(memory.export_state()).snapshot() == memory.snapshot()


def test_local_cas_publishes_once_with_concurrent_completion():
    memory = populated()
    job = memory.begin_compaction(request(memory))
    def complete():
        return memory.complete_compaction(job.job_id, "One result.", expected_revision=job.revision)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: complete(), range(2)))
    assert sorted(results) == ["applied", "duplicate"]
    assert memory.snapshot().revision == job.revision + 1


def test_restore_version_shape_and_payload_corruption_fail_closed():
    memory = populated()
    state = json.loads(memory.export_state())
    state["format"] = "future/2"
    with pytest.raises(MemoryError):
        ConversationMemory.from_state(json.dumps(state).encode())
    state = json.loads(memory.export_state())
    body = state["history"]["$tuple"][0]["fields"]["entries"]["$tuple"][0]["fields"]["payload"]
    body["$bytes"] = b"tampered".hex()
    with pytest.raises(MemoryError):
        ConversationMemory.from_state(json.dumps(state).encode())
    with pytest.raises(MemoryError):
        ConversationMemory.from_state(b'{"format":"a","format":"b"}')


def test_history_bound_is_not_silently_pruned_by_summary():
    limits = MemoryLimits(profile_id="small", max_groups=2, max_candidates=3, keep_recent_groups=1)
    memory = ConversationMemory(limits=limits)
    for i in range(2):
        memory.begin_turn(str(i), event_id=str(i))
        memory.close_turn(str(i))
    original = memory.snapshot()
    with pytest.raises(MemoryError, match="bounds"):
        memory.begin_turn("third", event_id="third")
    assert memory.snapshot() == original


def test_original_token_api_does_not_upgrade_advisory_counts():
    memory = populated()
    budget = ContextBudget(DEFAULT_MODEL, "a" * 64, SERIALIZER_DIGEST, 1000, 900, 50, 10)
    initial = memory.plan_tokens(budget=budget)
    assert initial.status == "need_count"
    advisory = CountEvidence(initial.candidate_digest, DEFAULT_MODEL, "estimate", 10, "text_only", None)
    assert memory.plan_tokens(budget=budget, counts=(advisory,)).status == "need_count"
    exact = CountEvidence(initial.candidate_digest, DEFAULT_MODEL, "exact", 10, "full_wire", "c" * 64)
    assert memory.plan_tokens(budget=budget, counts=(exact,)).status == "candidate"


def test_small_summary_limit_roundtrip_and_bad_container_types():
    memory = populated(MemoryLimits(profile_id="one-byte-summary", max_summary_bytes=1))
    job = memory.begin_compaction(request(memory))
    assert memory.complete_compaction(job.job_id, "x", expected_revision=job.revision) == "applied"
    assert ConversationMemory.from_state(memory.export_state()).snapshot() == memory.snapshot()
    state = json.loads(memory.export_state())
    for malformed in (1, {"$tuple": [1]}):
        state["history"] = malformed
        with pytest.raises(MemoryError):
            ConversationMemory.from_state(json.dumps(state).encode())
