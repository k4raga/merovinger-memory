"""Closed conversation-memory DATA. Digests/labels are never authority.

All payload bytes stay untrusted. No store, clock, model, tokenizer or callback.
The whole pure operation is bounded work OUTSIDE a C1/GUI semantic callback.
"""
from dataclasses import dataclass, fields
from hashlib import sha256
import json

U63_MAX = 2**63 - 1
DEFAULTS = (256, 2048, 32 * 1024 * 1024, 16 * 1024, 2048, 4, 257)
ERRORS = ("bounds", "shape", "coverage", "pairing", "foreign", "stale", "overflow")
KINDS = ("user_intent", "assistant_response", "invocation", "receipt", "effect_fact",
         "supersession", "tombstone")


class MemoryError(ValueError):
    """Closed diagnostics, containing no input or raw exception."""
    def __init__(self, code):
        if type(code) is not str or code not in ERRORS:
            raise ValueError("Unknown memory diagnostic")
        self.code = code
        super().__init__(code)


def require(test, code="shape"):
    if not test:
        raise MemoryError(code)


def uint(value, *, positive=False):
    require(type(value) is int and (1 if positive else 0) <= value <= U63_MAX, "bounds")


def name(value):
    require(type(value) is str and 0 < len(value) <= 256, "bounds")
    try:
        require(len(value.encode("utf-8")) <= 256, "bounds")
    except UnicodeError:
        raise MemoryError("bounds") from None


def digest(value, *, nullable=False):
    if value is None and nullable:
        return
    require(type(value) is str and len(value) == 64
            and all(char in "0123456789abcdef" for char in value), "bounds")


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")


def hash_data(value):
    return sha256(canonical(value)).hexdigest()


def plain(value):
    if any(type(value) is model for model in MODELS):
        return {f.name: plain(getattr(value, f.name)) for f in fields(value)}
    if type(value) is tuple:
        return [plain(item) for item in value]
    if type(value) is bytes:
        return {"bytes_hex": value.hex()}
    require(value is None or type(value) is str or type(value) is int)
    return value


def _copy(value):
    if any(type(value) is model for model in MODELS):
        result = object.__new__(type(value))
        for field in fields(value):
            object.__setattr__(result, field.name, _copy(getattr(value, field.name)))
        return result
    if type(value) is tuple:
        return tuple(_copy(item) for item in value)
    return value  # Only validated immutable primitives/bytes reach this point.


def record(value, cls):
    require(type(value) is cls)
    check(value)


def items(value, cls, maximum, *, nonempty=False):
    require(type(value) is tuple and (1 if nonempty else 0) <= len(value) <= maximum, "bounds")
    for item in value:
        record(item, cls)


class _Data:
    __slots__ = ()
    def __post_init__(self):
        check(self)
        for field in fields(self):
            object.__setattr__(self, field.name, _copy(getattr(self, field.name)))


@dataclass(frozen=True, slots=True)
class MemoryLimits(_Data):
    profile_id: str = "memory-v1"
    max_groups: int = 256
    max_entries: int = 2048
    max_window_bytes: int = 32 * 1024 * 1024
    max_summary_bytes: int = 16 * 1024
    max_source_refs: int = 2048
    keep_recent_groups: int = 4
    max_candidates: int = 257


@dataclass(frozen=True, slots=True)
class SourceKey(_Data):
    project_id: str
    conversation_id: str
    conversation_generation: int
    event_id: str
    sequence: int
    content_digest: str


@dataclass(frozen=True, slots=True)
class HistoryEntry(_Data):
    source: SourceKey
    group_id: str
    kind: str
    payload: bytes
    links: tuple[SourceKey, ...]
    provenance_digest: str


@dataclass(frozen=True, slots=True)
class HistoryGroup(_Data):
    group_id: str
    entries: tuple[HistoryEntry, ...]
    state: str


@dataclass(frozen=True, slots=True)
class RetainedGroupPin(_Data):
    group_id: str
    first_sequence: int
    entry_count: int
    entries_digest: str


@dataclass(frozen=True, slots=True)
class CoverageManifest(_Data):
    horizon: int
    prefix_digest: str
    retained_groups: tuple[RetainedGroupPin, ...]


@dataclass(frozen=True, slots=True)
class HistoryWindow(_Data):
    project_id: str
    conversation_id: str
    conversation_generation: int
    prefix_revision: int
    coverage_start: int
    ordered_groups: tuple[HistoryGroup, ...]
    latest_intent_key: SourceKey
    head_sequence: int
    active_fact_keys: tuple[SourceKey, ...]
    policy_digest: str
    base_coverage: CoverageManifest | None
    base_summary_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class ModelPin(_Data):
    provider: str
    model: str
    account: str
    config_digest: str
    tokenizer_digest: str | None
    serialization_digest: str


@dataclass(frozen=True, slots=True)
class CountEvidence(_Data):
    candidate_digest: str
    model_pin: ModelPin
    quality: str
    input_tokens: int | None
    scope: str
    wire_digest: str | None


@dataclass(frozen=True, slots=True)
class ContextBudget(_Data):
    model_pin: ModelPin
    fixed_context_digest: str
    summarizer_context_digest: str
    context_ceiling: int | None
    input_ceiling: int | None
    output_reservation: int
    safety_margin: int


@dataclass(frozen=True, slots=True)
class AttemptKey(_Data):
    project_id: str
    conversation_id: str
    conversation_generation: int
    prefix_revision: int
    prefix_start: int
    prefix_end: int
    prefix_digest: str
    parent_summary_digest: str | None
    policy_digest: str
    summarizer_pin: ModelPin
    summarizer_context_digest: str
    coverage_digest: str


@dataclass(frozen=True, slots=True)
class AttemptRecord(_Data):
    key: AttemptKey
    outcome: str


@dataclass(frozen=True, slots=True)
class CompactionDemand(_Data):
    key: AttemptKey
    source_keys: tuple[SourceKey, ...]
    target_coverage: CoverageManifest
    consumed_provenance_digest: str
    parent_provenance_digest: str | None
    input_digest: str
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class SummaryRecord(_Data):
    prefix_start: int
    prefix_end: int
    prefix_digest: str
    parent_summary_digest: str | None
    body: bytes
    provenance_digest: str
    policy_digest: str
    summarizer_pin: ModelPin
    attempt_key: AttemptKey
    coverage: CoverageManifest
    consumed_provenance_digest: str
    parent_provenance_digest: str | None
    demand_input_digest: str
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ContextPlan(_Data):
    status: str
    selected_group_ids: tuple[str, ...]
    protected_source_keys: tuple[SourceKey, ...]
    summary_fingerprint: str | None
    candidate_digest: str
    count_quality: str
    demand: CompactionDemand | None
    reason: str


MODELS = (MemoryLimits, SourceKey, HistoryEntry, HistoryGroup, RetainedGroupPin, CoverageManifest, HistoryWindow,
          ModelPin, CountEvidence, ContextBudget, AttemptKey, AttemptRecord,
          CompactionDemand, SummaryRecord, ContextPlan)
REASONS = ("within_budget", "count_required", "advisory_count", "unknown_ceiling",
           "window_unavailable", "missing_coverage", "summary_stale",
           "mandatory_exceeds_budget", "compaction_required", "trimmed",
           "candidate_limit", "attempt_recorded", "inflight_compaction", "requires_rebuild")


def identity(value):
    return value.project_id, value.conversation_id, value.conversation_generation


def summary_payload(value):
    return {"format": "memory-summary/1",
            **{f.name: plain(getattr(value, f.name)) for f in fields(value) if f.name != "fingerprint"}}


def coverage_digest(value):
    return hash_data({"format": "memory-coverage/1", "coverage": plain(value)})


def summary_provenance(consumed, parent):
    """Deterministic lineage DATA, never proof of actual taint/permission union."""
    return hash_data({"format": "memory-summary-provenance/2",
                      "consumed": consumed, "parent": parent})


def demand_input(key, coverage, source_keys, consumed, parent_provenance, maximum):
    return hash_data({"format": "memory-summary-input/2", "attempt": plain(key),
        "coverage": plain(coverage), "sources": plain(source_keys),
        "consumed_provenance": consumed, "parent": key.parent_summary_digest,
        "parent_provenance": parent_provenance, "max_output_bytes": maximum})


def check(value):
    cls = type(value)
    require(any(cls is model for model in MODELS))
    if cls is MemoryLimits:
        name(value.profile_id)
        vals = tuple(getattr(value, f.name) for f in fields(value) if f.name != "profile_id")
        for item, maximum in zip(vals, DEFAULTS):
            uint(item)
            require(item <= maximum, "bounds")
        require(all(v > 0 for i, v in enumerate(vals) if i != 5), "bounds")
        require(value.keep_recent_groups <= value.max_groups
                and value.max_candidates <= value.max_groups + 1, "bounds")
        if value.profile_id == "memory-v1":
            require(vals == DEFAULTS, "bounds")
    elif cls is SourceKey:
        for item in (value.project_id, value.conversation_id, value.event_id):
            name(item)
        uint(value.conversation_generation, positive=True); uint(value.sequence, positive=True)
        digest(value.content_digest)
    elif cls is HistoryEntry:
        record(value.source, SourceKey); name(value.group_id)
        require(type(value.kind) is str and value.kind in KINDS)
        require(type(value.payload) is bytes and len(value.payload) <= DEFAULTS[2], "bounds")
        require(sha256(value.payload).hexdigest() == value.source.content_digest)
        items(value.links, SourceKey, DEFAULTS[4]); digest(value.provenance_digest)
    elif cls is HistoryGroup:
        name(value.group_id)
        require(type(value.entries) is tuple and 0 < len(value.entries) <= DEFAULTS[1], "bounds")
        require(all(type(e) is HistoryEntry and type(e.payload) is bytes for e in value.entries))
        require(sum(len(e.payload) for e in value.entries) <= DEFAULTS[2], "bounds")
        require(all(type(e.links) is tuple for e in value.entries))
        require(sum(len(e.links) for e in value.entries) <= DEFAULTS[4], "bounds")
        items(value.entries, HistoryEntry, DEFAULTS[1], nonempty=True)
        require(type(value.state) is str and value.state in ("closed", "open"))
    elif cls is RetainedGroupPin:
        name(value.group_id); uint(value.first_sequence, positive=True)
        uint(value.entry_count, positive=True)
        require(value.entry_count <= DEFAULTS[1], "bounds")
        digest(value.entries_digest)
    elif cls is CoverageManifest:
        uint(value.horizon, positive=True); digest(value.prefix_digest)
        items(value.retained_groups, RetainedGroupPin, DEFAULTS[0])
        require(sum(pin.entry_count for pin in value.retained_groups) <= DEFAULTS[1], "bounds")
        require(len({pin.group_id for pin in value.retained_groups}) == len(value.retained_groups), "coverage")
        order = [(pin.first_sequence, pin.group_id) for pin in value.retained_groups]
        require(order == sorted(order), "coverage")
        require(all(pin.first_sequence <= value.horizon for pin in value.retained_groups), "coverage")
    elif cls is HistoryWindow:
        name(value.project_id); name(value.conversation_id)
        for item in (value.conversation_generation, value.prefix_revision,
                     value.coverage_start, value.head_sequence):
            uint(item, positive=True)
        require(type(value.ordered_groups) is tuple and 0 < len(value.ordered_groups) <= DEFAULTS[0], "bounds")
        require(all(type(g) is HistoryGroup and type(g.entries) is tuple for g in value.ordered_groups))
        require(sum(len(g.entries) for g in value.ordered_groups) <= DEFAULTS[1], "bounds")
        require(all(type(e) is HistoryEntry and type(e.payload) is bytes for g in value.ordered_groups for e in g.entries))
        require(sum(len(e.payload) for g in value.ordered_groups for e in g.entries) <= DEFAULTS[2], "bounds")
        require(all(type(e.links) is tuple for g in value.ordered_groups for e in g.entries))
        require(type(value.active_fact_keys) is tuple, "bounds")
        require(sum(len(e.links) for g in value.ordered_groups for e in g.entries)
            + len(value.active_fact_keys) + 1 <= DEFAULTS[4], "bounds")
        items(value.ordered_groups, HistoryGroup, DEFAULTS[0], nonempty=True)
        record(value.latest_intent_key, SourceKey)
        items(value.active_fact_keys, SourceKey, DEFAULTS[4]); digest(value.policy_digest)
        if value.base_coverage is not None:
            record(value.base_coverage, CoverageManifest)
        digest(value.base_summary_fingerprint, nullable=True)
        require((value.base_coverage is None) == (value.base_summary_fingerprint is None))
    elif cls is ModelPin:
        for item in (value.provider, value.model, value.account):
            name(item)
        digest(value.config_digest); digest(value.tokenizer_digest, nullable=True)
        digest(value.serialization_digest)
    elif cls is CountEvidence:
        digest(value.candidate_digest); record(value.model_pin, ModelPin)
        require(type(value.quality) is str and value.quality in ("exact", "upper_bound", "estimate", "unknown"))
        require(type(value.scope) is str and value.scope in ("full_wire", "text_only"))
        if value.quality == "unknown":
            require(value.input_tokens is None)
        else:
            uint(value.input_tokens)
        digest(value.wire_digest, nullable=True)
        if value.scope == "text_only":
            require(value.wire_digest is None)
        elif value.quality in ("exact", "upper_bound"):
            require(value.wire_digest is not None)
    elif cls is ContextBudget:
        record(value.model_pin, ModelPin)
        digest(value.fixed_context_digest); digest(value.summarizer_context_digest)
        for item in (value.context_ceiling, value.input_ceiling):
            if item is not None:
                uint(item, positive=True)
        uint(value.output_reservation); uint(value.safety_margin)
        require(value.output_reservation <= U63_MAX - value.safety_margin, "overflow")
    elif cls is AttemptKey:
        name(value.project_id); name(value.conversation_id)
        for item in (value.conversation_generation, value.prefix_revision,
                     value.prefix_start, value.prefix_end):
            uint(item, positive=True)
        require(value.prefix_start == 1 and value.prefix_end >= 1)
        digest(value.prefix_digest); digest(value.parent_summary_digest, nullable=True)
        digest(value.policy_digest); record(value.summarizer_pin, ModelPin)
        digest(value.summarizer_context_digest); digest(value.coverage_digest)
    elif cls is AttemptRecord:
        record(value.key, AttemptKey)
        require(type(value.outcome) is str and value.outcome in
                ("inflight", "accepted", "failed", "unknown", "overflow", "stale"))
    elif cls is CompactionDemand:
        record(value.key, AttemptKey); items(value.source_keys, SourceKey, DEFAULTS[4], nonempty=True)
        record(value.target_coverage, CoverageManifest)
        key = value.key
        require(all(identity(k) == identity(key) for k in value.source_keys), "foreign")
        seqs = [k.sequence for k in value.source_keys]
        require(seqs == sorted(set(seqs)) and seqs[-1] <= key.prefix_end, "coverage")
        require(len({k.event_id for k in value.source_keys}) == len(value.source_keys), "coverage")
        require(key.prefix_end == value.target_coverage.horizon
                and key.prefix_digest == value.target_coverage.prefix_digest
                and key.coverage_digest == coverage_digest(value.target_coverage))
        digest(value.consumed_provenance_digest); digest(value.parent_provenance_digest, nullable=True)
        require((key.parent_summary_digest is None) == (value.parent_provenance_digest is None))
        uint(value.max_output_bytes, positive=True)
        require(value.max_output_bytes <= DEFAULTS[3], "bounds")
        digest(value.input_digest)
        require(value.input_digest == demand_input(key, value.target_coverage, value.source_keys,
            value.consumed_provenance_digest, value.parent_provenance_digest, value.max_output_bytes))
    elif cls is SummaryRecord:
        record(value.attempt_key, AttemptKey)
        key = value.attempt_key
        for item in (value.prefix_start, value.prefix_end):
            uint(item, positive=True)
        digest(value.prefix_digest); digest(value.parent_summary_digest, nullable=True)
        digest(value.policy_digest); record(value.summarizer_pin, ModelPin)
        require((value.prefix_start, value.prefix_end, value.prefix_digest, value.parent_summary_digest,
                 value.policy_digest, value.summarizer_pin) ==
                (key.prefix_start, key.prefix_end, key.prefix_digest, key.parent_summary_digest,
                 key.policy_digest, key.summarizer_pin))
        digest(value.prefix_digest); digest(value.parent_summary_digest, nullable=True)
        digest(value.provenance_digest)
        record(value.coverage, CoverageManifest)
        require(key.coverage_digest == coverage_digest(value.coverage)
                and key.prefix_end == value.coverage.horizon
                and key.prefix_digest == value.coverage.prefix_digest)
        digest(value.consumed_provenance_digest); digest(value.parent_provenance_digest, nullable=True)
        require((key.parent_summary_digest is None) == (value.parent_provenance_digest is None))
        require(value.provenance_digest == summary_provenance(
            value.consumed_provenance_digest, value.parent_provenance_digest))
        digest(value.policy_digest); record(value.summarizer_pin, ModelPin)
        require(type(value.body) is bytes and len(value.body) <= DEFAULTS[3], "bounds")
        try:
            text = value.body.decode("utf-8")
        except UnicodeError:
            raise MemoryError("shape") from None
        # Bound escaped text as well. Actual 32 KiB runner response is validated
        # again by the future summary-purpose codec, including usage/metadata.
        require(len(canonical({"text": text, "proposals": []})) <= 32 * 1024, "bounds")
        require(hasattr(value, "demand_input_digest"))
        digest(value.demand_input_digest)
        digest(value.fingerprint)
        require(value.fingerprint == hash_data(summary_payload(value)))
    elif cls is ContextPlan:
        require(type(value.status) is str and value.status in
                ("candidate", "need_count", "need_compaction", "cannot_fit", "unavailable"))
        require(type(value.selected_group_ids) is tuple and len(value.selected_group_ids) <= DEFAULTS[0], "bounds")
        for item in value.selected_group_ids:
            name(item)
        items(value.protected_source_keys, SourceKey, DEFAULTS[4])
        digest(value.summary_fingerprint, nullable=True); digest(value.candidate_digest)
        require(type(value.count_quality) is str and value.count_quality in ("exact", "upper_bound", "estimate", "unknown"))
        if value.demand is not None:
            record(value.demand, CompactionDemand)
        require((value.status == "need_compaction") == (value.demand is not None))
        require(type(value.reason) is str and value.reason in REASONS)
