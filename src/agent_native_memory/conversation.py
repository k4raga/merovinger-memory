"""Bounded text history owned by an external application, never a C1 mirror.

No file IO, provider, scheduler or durable exactly-once claim. Methods are
serialized by one in-process lock; callers persist exported bytes themselves.
Byte preparation measures only the returned application context, not tokens.
"""
from dataclasses import dataclass, fields, replace
from hashlib import sha256
from threading import RLock
import json

from ._values import (
    MODELS, MemoryError, MemoryLimits, SourceKey, HistoryEntry, HistoryGroup,
    HistoryWindow, ModelPin, AttemptRecord, SummaryRecord, CompactionDemand,
    canonical, hash_data, plain, require, record, name, digest, uint,
)
from ._window import _inspect, _summary_matches, validate_window
from ._planner import (
    _compaction_for_overflow, make_summary, check_summary_applicability, plan_context,
)

FORMAT = "agent-native-memory.external-state/1"
SERIALIZER_DIGEST = hash_data({"format": "external-text-context/1", "json": "ascii-sorted/1"})
DEFAULT_POLICY = hash_data({"policy": "caller-authorized-external-history/1"})
DEFAULT_MODEL = ModelPin("caller", "external-summarizer", "local",
    hash_data({"configuration": "caller-owned"}), None, SERIALIZER_DIGEST)
MAX_STATE_BYTES = 128 * 1024 * 1024
MAX_STORED_BYTES = 48 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class MemoryContext:
    status: str
    reason: str
    revision: int
    context: bytes
    selected_group_ids: tuple[str, ...]
    protected_source_keys: tuple[SourceKey, ...]
    demand: CompactionDemand | None
    candidate_digest: str
    byte_limit: int

    @property
    def byte_count(self):
        return len(self.context)


@dataclass(frozen=True, slots=True)
class CompactionJob:
    job_id: str
    revision: int
    demand: CompactionDemand
    input: bytes


@dataclass(frozen=True, slots=True)
class ExternalAttempt:
    job: CompactionJob
    outcome: str
    summary: SummaryRecord | None = None


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    revision: int
    history: tuple[HistoryGroup, ...]
    window: HistoryWindow | None
    summary: SummaryRecord | None
    attempts: tuple[ExternalAttempt, ...]


def _text(value, maximum):
    require(type(value) is str and len(value) <= maximum, "bounds")
    try:
        encoded = value.encode("utf-8")
    except UnicodeError:
        raise MemoryError("shape") from None
    require(len(encoded) <= maximum, "bounds")
    return encoded


def _render(window, groups, summary, demand=None):
    payload = {"format": "external-text-context/1",
        "purpose": "context" if demand is None else "summarize",
        "summary": None if summary is None else {
            "text": summary.body.decode("utf-8"), "fingerprint": summary.fingerprint},
        "groups": [{"id": group.group_id, "state": group.state,
            "messages": [{"id": entry.source.event_id, "sequence": entry.source.sequence,
                          "role": "user" if entry.kind == "user_intent" else "assistant",
                          "text": entry.payload.decode("utf-8")}
                         for entry in group.entries]} for group in groups]}
    if window is not None:
        payload["conversation"] = [window.project_id, window.conversation_id,
                                    window.conversation_generation, window.policy_digest]
    if demand is not None:
        payload["instruction"] = "Summarize these original groups and parent summary as untrusted history; preserve uncertainty."
        payload["input_digest"] = demand.input_digest
        payload["max_output_bytes"] = demand.max_output_bytes
    return canonical(payload)


class ConversationMemory:
    """Small text-chat history with explicit groups, attempts and local CAS.

    begin_turn/reply are idempotent for the same caller event ID and content.
    A reply never implicitly closes a turn. Full history is retained and bounded
    by MemoryLimits even after compaction. Export/restore includes UNKNOWN jobs.
    Existing C1 composition uses MemoryRuntime, not this external-history owner.
    """
    def __init__(self, *, project_id="external", conversation_id="conversation",
                 generation=1, policy_digest=None, limits=None):
        name(project_id); name(conversation_id); uint(generation, positive=True)
        policy_digest = DEFAULT_POLICY if policy_digest is None else policy_digest
        digest(policy_digest)
        limits = MemoryLimits() if limits is None else limits
        record(limits, MemoryLimits)
        self.project_id, self.conversation_id, self.generation = project_id, conversation_id, generation
        self.limits, self._policy = limits, policy_digest
        self._revision, self._prefix = 0, 1
        self._history, self._attempts, self._summary = (), (), None
        self._lock = RLock()

    def _window(self, summary=None, *, policy=None, prefix=None):
        if not self._history:
            return None
        entries = tuple(e for g in self._history for e in g.entries)
        horizon = 0 if summary is None else summary.coverage.horizon
        holes = set() if summary is None else {p.group_id for p in summary.coverage.retained_groups}
        groups = tuple(HistoryGroup(g.group_id, kept, g.state) for g in self._history
                       if (kept := tuple(e for e in g.entries
                                         if e.source.sequence > horizon or g.group_id in holes)))
        latest = max((e.source for e in entries if e.kind == "user_intent"), key=lambda k: k.sequence)
        return HistoryWindow(self.project_id, self.conversation_id, self.generation,
            self._prefix if prefix is None else prefix, horizon + 1, groups, latest,
            max(e.source.sequence for e in entries), (), self._policy if policy is None else policy,
            None if summary is None else summary.coverage,
            None if summary is None else summary.fingerprint)

    def snapshot(self):
        with self._lock:
            window = self._window(self._summary)
            if window is not None:
                validate_window(window, limits=self.limits)
                require(_summary_matches(window, self._summary), "stale")
            return ConversationSnapshot(self._revision, self._history, window,
                                        self._summary, self._attempts)

    def _check_storage(self, history, attempts):
        require(len(history) <= self.limits.max_groups and
                sum(len(g.entries) for g in history) <= self.limits.max_entries, "bounds")
        raw = sum(len(e.payload) for g in history for e in g.entries)
        require(raw <= self.limits.max_window_bytes, "bounds")
        require(len(attempts) <= self.limits.max_candidates, "bounds")
        require(raw + sum(len(a.job.input) + (0 if a.summary is None else len(a.summary.body))
                          for a in attempts) <= MAX_STORED_BYTES, "bounds")

    def _append(self, group_id, text, event_id, kind):
        name(group_id); name(event_id)
        body = _text(text, self.limits.max_window_bytes)
        with self._lock:
            group = next((g for g in self._history if g.group_id == group_id), None)
            links = () if kind == "user_intent" else (() if group is None else (group.entries[0].source,))
            old = next((e for g in self._history for e in g.entries if e.source.event_id == event_id), None)
            if old is not None:
                require((old.group_id, old.kind, old.payload, old.links) ==
                        (group_id, kind, body, links), "foreign")
                return group_id
            if kind == "user_intent":
                require(group is None, "pairing")
            else:
                require(group is not None and group.state == "open", "pairing")
            sequence = sum(len(g.entries) for g in self._history) + 1
            key = SourceKey(self.project_id, self.conversation_id, self.generation,
                            event_id, sequence, sha256(body).hexdigest())
            entry = HistoryEntry(key, group_id, kind, body, links,
                                 hash_data({"external_source": plain(key)}))
            updated = HistoryGroup(group_id, (entry,) if group is None else (*group.entries, entry), "open")
            history = (*self._history, updated) if group is None else tuple(
                updated if g.group_id == group_id else g for g in self._history)
            self._check_storage(history, self._attempts)
            self._history = history
            self._revision += 1
            return group_id

    def begin_turn(self, text, *, event_id, turn_id=None):
        return self._append(event_id if turn_id is None else turn_id, text, event_id, "user_intent")

    user = begin_turn

    def reply(self, turn_id, text, *, event_id):
        return self._append(turn_id, text, event_id, "assistant_response")

    def close_turn(self, turn_id):
        name(turn_id)
        with self._lock:
            require(any(g.group_id == turn_id for g in self._history), "foreign")
            history = tuple(replace(g, state="closed") if g.group_id == turn_id else g for g in self._history)
            if history != self._history:
                self._history = history
                self._revision += 1

    def set_policy(self, policy_digest):
        """Invalidate accepted selection; never interpret a digest as permission."""
        digest(policy_digest)
        with self._lock:
            if self._policy != policy_digest:
                self._policy, self._summary = policy_digest, None
                self._prefix += 1
                self._revision += 1

    def prepare(self, *, byte_limit, allow_compaction=True, summarizer_pin=None):
        uint(byte_limit, positive=True)
        require(byte_limit <= self.limits.max_window_bytes, "bounds")
        require(type(allow_compaction) is bool)
        pin = DEFAULT_MODEL if summarizer_pin is None else summarizer_pin
        record(pin, ModelPin)
        snapshot = self.snapshot()
        window, summary = snapshot.window, snapshot.summary
        groups = () if window is None else window.ordered_groups
        inv = None if window is None else _inspect(window, self.limits)
        protected = () if inv is None else inv.protected

        def result(status, reason, selected, raw, demand=None):
            candidate = hash_data({"format": "external-byte-candidate/1", "revision": snapshot.revision,
                "serializer": SERIALIZER_DIGEST, "byte_limit": byte_limit,
                "raw": sha256(raw).hexdigest(), "demand": None if demand is None else demand.input_digest})
            return MemoryContext(status, reason, snapshot.revision, raw,
                tuple(g.group_id for g in selected), protected, demand, candidate, byte_limit)

        raw = _render(window, groups, summary)
        if window is None:
            return result("empty" if len(raw) <= byte_limit else "cannot_fit", "empty_history", (), raw)
        minimum = tuple(g for g in groups if g.group_id in inv.mandatory_groups)
        minimum_raw = _render(window, minimum, summary)
        if len(minimum_raw) > byte_limit:
            return result("cannot_fit", "mandatory_exceeds_bytes", minimum, minimum_raw)
        if len(raw) <= byte_limit:
            return result("ready", "within_bytes", groups, raw)
        attempts = tuple(AttemptRecord(a.job.demand.key, a.outcome) for a in snapshot.attempts)
        unresolved = any(a.outcome in ("inflight", "unknown") for a in snapshot.attempts)
        if allow_compaction and not unresolved:
            boundaries = sorted({max(e.source.sequence for e in g.entries) for g in groups
                                 if g.state == "closed" and g.group_id not in inv.mandatory_groups}, reverse=True)
            for fence in boundaries[:self.limits.max_candidates]:
                if summary is not None and fence < summary.coverage.horizon:
                    continue
                demand = _compaction_for_overflow(window, summary=summary, summarizer_pin=pin,
                    summarizer_context_digest=SERIALIZER_DIGEST, attempts=attempts,
                    limits=self.limits, prefix_end_cap=fence, prefer_recent=False)
                if demand is None:
                    continue
                sources = set(demand.source_keys)
                consumed = tuple(g for g in groups if any(e.source in sources for e in g.entries))
                raw = _render(window, consumed, summary, demand)
                if len(raw) <= byte_limit:
                    return result("needs_compaction", "compaction_required", consumed, raw, demand)
        selected = groups
        for _ in range(self.limits.max_candidates):
            removable = next((g for g in selected if g.group_id not in inv.mandatory_groups), None)
            if removable is None:
                break
            selected = tuple(g for g in selected if g is not removable)
            raw = _render(window, selected, summary)
            if len(raw) <= byte_limit:
                return result("ready", "trimmed_whole_groups", selected, raw)
        return result("cannot_fit", "mandatory_exceeds_bytes", minimum, minimum_raw)

    def begin_compaction(self, prepared):
        """Register inflight atomically BEFORE caller starts its summarizer."""
        require(type(prepared) is MemoryContext and prepared.status == "needs_compaction")
        with self._lock:
            require(prepared.revision == self._revision, "stale")
            require(not any(a.outcome in ("inflight", "unknown") for a in self._attempts), "stale")
            record(prepared.demand, CompactionDemand)
            window = self._window(self._summary)
            demand = _compaction_for_overflow(window, summary=self._summary,
                summarizer_pin=prepared.demand.key.summarizer_pin,
                summarizer_context_digest=SERIALIZER_DIGEST,
                attempts=tuple(AttemptRecord(a.job.demand.key, a.outcome) for a in self._attempts),
                limits=self.limits, prefix_end_cap=prepared.demand.key.prefix_end, prefer_recent=False)
            require(demand == prepared.demand, "stale")
            sources = set(demand.source_keys)
            consumed = tuple(g for g in window.ordered_groups if any(e.source in sources for e in g.entries))
            require(prepared.context == _render(window, consumed, self._summary, demand))
            require(len(prepared.context) <= prepared.byte_limit, "bounds")
            job = CompactionJob(demand.input_digest, self._revision + 1, demand, prepared.context)
            attempts = (*self._attempts, ExternalAttempt(job, "inflight"))
            self._check_storage(self._history, attempts)
            self._attempts = attempts
            self._revision += 1
            return job

    def _attempt(self, job_id):
        digest(job_id)
        match = next((a for a in self._attempts if a.job.job_id == job_id), None)
        require(match is not None, "foreign")
        return match

    def _replace_attempt(self, attempt):
        self._attempts = tuple(attempt if a.job.job_id == attempt.job.job_id else a for a in self._attempts)
        self._revision += 1

    def complete_compaction(self, job_id, text, *, expected_revision):
        uint(expected_revision)
        with self._lock:
            attempt = self._attempt(job_id)
            summary = make_summary(attempt.job.demand, _text(text, attempt.job.demand.max_output_bytes))
            if attempt.outcome == "accepted":
                return "duplicate" if summary == attempt.summary else "collision"
            if attempt.outcome not in ("inflight", "unknown"):
                return "stale"
            if expected_revision != self._revision:
                return "stale"
            decision = check_summary_applicability(attempt.job.demand, summary,
                                                   self._window(self._summary), self._summary)
            if decision != "applicable":
                self._replace_attempt(replace(attempt, outcome="stale"))
                return decision
            updated = replace(attempt, outcome="accepted", summary=summary)
            attempts = tuple(updated if a is attempt else a for a in self._attempts)
            self._check_storage(self._history, attempts)
            self._summary, self._attempts = summary, attempts
            self._revision += 1
            return "applied"

    def _finish_attempt(self, job_id, outcome):
        with self._lock:
            attempt = self._attempt(job_id)
            if attempt.outcome == outcome:
                return outcome
            require(attempt.outcome in ("inflight", "unknown"), "stale")
            self._replace_attempt(replace(attempt, outcome=outcome))
            return outcome

    def mark_unknown(self, job_id):
        return self._finish_attempt(job_id, "unknown")

    def fail_compaction(self, job_id):
        """Caller explicitly confirms failure; does not authorize an automatic retry."""
        return self._finish_attempt(job_id, "failed")

    def plan_tokens(self, *, budget, counts=()):
        """Original token planner; only caller full-wire exact/upper-bound evidence counts."""
        snap = self.snapshot()
        require(snap.window is not None, "coverage")
        return plan_context(snap.window, summary=snap.summary, budget=budget, counts=counts,
            attempts=tuple(AttemptRecord(a.job.demand.key, a.outcome) for a in snap.attempts),
            limits=self.limits)

    def export_state(self):
        """Return versioned bytes; caller owns atomic file writes and durability."""
        with self._lock:
            value = {"format": FORMAT, "project": self.project_id, "conversation": self.conversation_id,
                "generation": self.generation, "policy": self._policy, "revision": self._revision,
                "prefix": self._prefix, "limits": self.limits, "history": self._history,
                "summary": self._summary, "attempts": self._attempts}
            encoded = canonical(_pack(value))
            require(len(encoded) <= MAX_STATE_BYTES, "bounds")
            return encoded

    @classmethod
    def from_state(cls, data):
        require(type(data) is bytes and len(data) <= MAX_STATE_BYTES, "bounds")
        try:
            raw = json.loads(data, object_pairs_hook=_unique)
            value = _unpack(raw)
            require(type(value) is dict and set(value) == {"format", "project", "conversation",
                "generation", "policy", "revision", "prefix", "limits", "history", "summary", "attempts"})
            require(value["format"] == FORMAT)
            self = cls(project_id=value["project"], conversation_id=value["conversation"],
                       generation=value["generation"], policy_digest=value["policy"], limits=value["limits"])
            uint(value["revision"]); uint(value["prefix"], positive=True)
            self._revision, self._prefix = value["revision"], value["prefix"]
            self._history, self._attempts, self._summary = value["history"], value["attempts"], value["summary"]
            self._validate_restored()
            return self
        except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError, OverflowError):
            raise MemoryError("shape") from None

    def _validate_restored(self):
        require(type(self._history) is tuple and type(self._attempts) is tuple)
        for group in self._history:
            record(group, HistoryGroup)
        for attempt in self._attempts:
            require(type(attempt) is ExternalAttempt and type(attempt.job) is CompactionJob)
            require(type(attempt.job.input) is bytes)
            if attempt.summary is not None:
                record(attempt.summary, SummaryRecord)
        self._check_storage(self._history, self._attempts)
        for group in self._history:
            record(group, HistoryGroup)
            require(group.entries[0].kind == "user_intent")
            for entry in group.entries:
                require(entry.kind in ("user_intent", "assistant_response"))
                entry.payload.decode("utf-8")
                require(entry.provenance_digest == hash_data({"external_source": plain(entry.source)}))
        base = self._window()
        if base is not None:
            validate_window(base, limits=self.limits)
        else:
            require(not self._attempts and self._summary is None)
        known = {}
        identifiers = set()
        for attempt in self._attempts:
            require(type(attempt) is ExternalAttempt and type(attempt.job) is CompactionJob)
            job = attempt.job
            record(job.demand, CompactionDemand)
            record(AttemptRecord(job.demand.key, attempt.outcome), AttemptRecord)
            require(job.job_id == job.demand.input_digest and job.job_id not in identifiers)
            identifiers.add(job.job_id)
            uint(job.revision, positive=True)
            require(job.revision <= self._revision and type(job.input) is bytes)
            parent_id = job.demand.key.parent_summary_digest
            require(parent_id is None or parent_id in known, "coverage")
            parent = known.get(parent_id)
            key = job.demand.key
            window = self._window(parent, policy=key.policy_digest, prefix=key.prefix_revision)
            candidate = make_summary(job.demand, b"")
            require(check_summary_applicability(job.demand, candidate, window, parent) == "applicable", "coverage")
            sources = set(job.demand.source_keys)
            consumed = tuple(g for g in window.ordered_groups if any(e.source in sources for e in g.entries))
            require(job.input == _render(window, consumed, parent, job.demand))
            if attempt.outcome == "accepted":
                record(attempt.summary, SummaryRecord)
                require(attempt.summary == make_summary(job.demand, attempt.summary.body))
                known[attempt.summary.fingerprint] = attempt.summary
            else:
                require(attempt.summary is None)
        require(sum(a.outcome in ("inflight", "unknown") for a in self._attempts) <= 1)
        if self._summary is not None:
            record(self._summary, SummaryRecord)
            require(known.get(self._summary.fingerprint) == self._summary)
        self.snapshot()


_CLASSES = {model.__name__: model for model in (*MODELS, CompactionJob, ExternalAttempt)}


def _pack(value):
    if type(value).__name__ in _CLASSES and type(value) is _CLASSES[type(value).__name__]:
        return {"$type": type(value).__name__, "fields": {f.name: _pack(getattr(value, f.name)) for f in fields(value)}}
    if type(value) is bytes:
        return {"$bytes": value.hex()}
    if type(value) is tuple:
        return {"$tuple": [_pack(v) for v in value]}
    if type(value) is dict:
        return {key: _pack(v) for key, v in value.items()}
    require(value is None or type(value) in (str, int))
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def _unpack(value, depth=0):
    require(depth <= 32, "bounds")
    if type(value) is not dict:
        require(value is None or type(value) in (str, int))
        return value
    if set(value) == {"$bytes"}:
        require(type(value["$bytes"]) is str and len(value["$bytes"]) <= 2 * MAX_STORED_BYTES, "bounds")
        return bytes.fromhex(value["$bytes"])
    if set(value) == {"$tuple"}:
        require(type(value["$tuple"]) is list and len(value["$tuple"]) <= 2048, "bounds")
        return tuple(_unpack(v, depth + 1) for v in value["$tuple"])
    if set(value) == {"$type", "fields"}:
        require(type(value["$type"]) is str and value["$type"] in _CLASSES)
        model = _CLASSES[value["$type"]]
        require(type(value["fields"]) is dict and set(value["fields"]) == {f.name for f in fields(model)})
        return model(**{k: _unpack(v, depth + 1) for k, v in value["fields"].items()})
    require(all(type(k) is str and not k.startswith("$") for k in value))
    return {k: _unpack(v, depth + 1) for k, v in value.items()}
