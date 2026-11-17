"""Pure memory advice and applicability. No inference, clocks, store or SEND.

Metadata commitments are precomputed once per window; candidate enumeration
never rehashes payload. Whole planning is bounded work outside C1/GUI callbacks.
"""
from dataclasses import fields
from ._values import (
    MemoryError, MemoryLimits, ModelPin, CountEvidence, ContextBudget, AttemptKey,
    AttemptRecord, CompactionDemand, SummaryRecord, ContextPlan, U63_MAX,
    require, record, items, plain, canonical, hash_data, identity, coverage_digest, digest, uint,
    demand_input, summary_payload, summary_provenance,
)
from ._window import _inspect, _validated_inventory, _summary_matches, _target, _consumed


def _candidate(window, selected, summary, budget, limits, inv):
    return hash_data({"format": "memory-candidate/1", "identity": list(identity(window)),
        "prefix_revision": window.prefix_revision, "coverage_start": window.coverage_start,
        "budget": plain(budget), "limits": plain(limits),
        "groups": [inv.group_digests[g.group_id] for g in selected],
        "base_coverage": plain(window.base_coverage),
        "base_summary": window.base_summary_fingerprint,
        "summary": None if summary is None else summary.fingerprint,
        "summary_coverage": None if summary is None else plain(summary.coverage),
        "latest_intent": plain(window.latest_intent_key),
        "protected": [inv.entry_digests[k] for k in inv.protected],
        "policy": window.policy_digest})


def _evidence_records(counts, limits):
    items(counts, CountEvidence, limits.max_candidates)
    groups = {}
    for count in counts:
        groups.setdefault((count.candidate_digest, count.model_pin, count.scope), []).append(count)
    for group in groups.values():
        exact = {e.input_tokens for e in group if e.quality == "exact"}
        require(len(exact) <= 1)
        hard = [e for e in group if e.quality in ("exact", "upper_bound")]
        if group[0].scope == "full_wire":
            require(len({e.wire_digest for e in hard}) <= 1)
        if exact:
            require(all(next(iter(exact)) <= e.input_tokens for e in group if e.quality == "upper_bound"))
        seen = {}
        for e in group:
            k = e.quality, e.wire_digest
            require(k not in seen or seen[k] == e.input_tokens)
            seen[k] = e.input_tokens


def _count(candidate, pin, counts):
    matches = [e for e in counts if e.candidate_digest == candidate and e.model_pin == pin]
    hard = [e for e in matches if e.scope == "full_wire" and e.quality in ("exact", "upper_bound")]
    if hard:
        exact = [e for e in hard if e.quality == "exact"]
        selected = exact[0] if exact else max(hard, key=lambda e: e.input_tokens)
        return selected.input_tokens, selected.quality
    for quality in ("exact", "upper_bound", "estimate"):
        if any(e.quality == quality for e in matches):
            return None, quality
    return None, "unknown"


def _fit(count, budget):
    reserve = budget.output_reservation + budget.safety_margin
    return (count <= budget.input_ceiling and count <= U63_MAX - reserve
        and count + reserve <= budget.context_ceiling)


def _semantic(key):
    # Empty policy/prefix-revision churn is not new source work. Full attempt
    # identity still pins those fields for CAS. Prefix hash commits immutable
    # inventory, not policy/revision labels; coverage pins holes separately.
    return (identity(key), key.prefix_digest, key.coverage_digest,
        key.parent_summary_digest, key.summarizer_pin, key.summarizer_context_digest)


def _attempt_records(attempts, window, limits):
    items(attempts, AttemptRecord, limits.max_candidates)
    seen = {}
    for attempt in attempts:
        require(identity(attempt.key) == identity(window), "foreign")
        require(attempt.key not in seen or seen[attempt.key] == attempt.outcome)
        seen[attempt.key] = attempt.outcome
    require(sum(value == "inflight" for value in seen.values()) <= 1)
    return seen


def _new_demand(window, summary, summarizer_pin, summarizer_context_digest, limits, inv, attempts,
                prefix_end_cap=None, *, prefer_recent=True):
    require(type(prefer_recent) is bool, "shape")
    if "inflight" in attempts.values():
        return None
    groups = window.ordered_groups
    recent = ({g.group_id for g in groups[-limits.keep_recent_groups:]}
              if prefer_recent and limits.keep_recent_groups else set())
    eligible = [g for g in groups if g.state == "closed" and g.group_id not in inv.mandatory_groups]
    if prefix_end_cap is not None:
        eligible = [g for g in eligible if max(e.source.sequence for e in g.entries) <= prefix_end_cap]
    preferred = [g for g in eligible if g.group_id not in recent]
    if preferred:
        horizon = max(max(e.source.sequence for e in g.entries) for g in preferred)
    elif eligible:
        # Recent retention is a preference. If it alone blocks progress,
        # consume the oldest eligible whole group, not individual messages.
        oldest = eligible[0]
        horizon = max(e.source.sequence for e in oldest.entries)
        recent.discard(oldest.group_id)
    else:
        return None
    old = 0 if window.base_coverage is None else window.base_coverage.horizon
    horizon = max(horizon, old)
    holes = {g.group_id for g in groups if any(e.source.sequence <= horizon for e in g.entries)
        and (g.state == "open" or g.group_id in inv.mandatory_groups or g.group_id in recent
             or any(e.source.sequence > horizon for e in g.entries))}
    coverage = _target(window, inv, horizon, holes)
    sources, consumed = _consumed(window, inv, horizon, holes)
    if not sources:
        return None
    key = AttemptKey(window.project_id, window.conversation_id, window.conversation_generation,
        window.prefix_revision, 1, horizon, coverage.prefix_digest,
        None if summary is None else summary.fingerprint, window.policy_digest,
        summarizer_pin, summarizer_context_digest, coverage_digest(coverage))
    if any(_semantic(prior) == _semantic(key) for prior in attempts):
        return None
    parent_provenance = None if summary is None else summary.provenance_digest
    maximum = limits.max_summary_bytes
    return CompactionDemand(key, sources, coverage, consumed, parent_provenance,
        demand_input(key, coverage, sources, consumed, parent_provenance, maximum), maximum)


def _compaction_for_overflow(window, *, summary, summarizer_pin,
                             summarizer_context_digest, attempts, limits,
                             prefix_end_cap=None, prefer_recent=True):
    """Private explicit-overflow advice, sharing the hard planner's topology.

    No token evidence, fake budget, store or inference is created here. A cap
    limits eligible whole-group ends; accepted base coverage is never reduced.
    Default None preserves the original hard-planner demand exactly.
    Visible overflow may soften recent retention; mandatory holes stay intact.
    """
    require(type(prefer_recent) is bool, "shape")
    record(limits, MemoryLimits); record(summarizer_pin, ModelPin)
    digest(summarizer_context_digest)
    if summary is not None:
        record(summary, SummaryRecord)
        require(len(summary.body) <= limits.max_summary_bytes, "bounds")
    inv = _inspect(window, limits)
    require(_summary_matches(window, summary), "stale")
    tried = _attempt_records(attempts, window, limits)
    if prefix_end_cap is not None:
        uint(prefix_end_cap, positive=True)
        old = 0 if window.base_coverage is None else window.base_coverage.horizon
        require(old <= prefix_end_cap <= window.head_sequence, "bounds")
    return _new_demand(window, summary, summarizer_pin, summarizer_context_digest,
                       limits, inv, tried, prefix_end_cap, prefer_recent=prefer_recent)


def _plan(status, groups, protected, summary, candidate, quality, demand, reason):
    return ContextPlan(status, tuple(g.group_id for g in groups), protected,
        None if summary is None else summary.fingerprint, candidate, quality, demand, reason)


def plan_context(window, *, summary, budget, counts, attempts, limits):
    """Deterministic planning DATA, not ready_to_send or verified wire fit."""
    record(limits, MemoryLimits); record(budget, ContextBudget)
    if summary is not None:
        record(summary, SummaryRecord)
        require(len(summary.body) <= limits.max_summary_bytes, "bounds")
    _evidence_records(counts, limits)
    try:
        inv = _inspect(window, limits)
    except MemoryError as error:
        if error.code not in ("bounds", "coverage", "stale"):
            raise
        reason = {"bounds": "window_unavailable", "coverage": "missing_coverage",
                  "stale": "requires_rebuild"}[error.code]
        return _plan("unavailable", (), (), None,
            hash_data({"format": "memory-unavailable/1", "budget": plain(budget), "limits": plain(limits)}),
            "unknown", None, reason)
    tried = _attempt_records(attempts, window, limits)
    if not _summary_matches(window, summary):
        return _plan("unavailable", (), inv.protected, None,
            _candidate(window, (), None, budget, limits, inv), "unknown", None, "summary_stale")
    selected = window.ordered_groups
    if budget.context_ceiling is None or budget.input_ceiling is None:
        return _plan("unavailable", selected, inv.protected, summary,
            _candidate(window, selected, summary, budget, limits, inv), "unknown", None, "unknown_ceiling")
    minimum = tuple(g for g in selected if g.group_id in inv.mandatory_groups)
    smallest = _candidate(window, minimum, summary, budget, limits, inv)
    tokens, quality = _count(smallest, budget.model_pin, counts)
    if tokens is not None and not _fit(tokens, budget):
        return _plan("cannot_fit", minimum, inv.protected, summary, smallest, quality,
                     None, "mandatory_exceeds_budget")
    for number in range(limits.max_candidates):
        candidate = _candidate(window, selected, summary, budget, limits, inv)
        tokens, quality = _count(candidate, budget.model_pin, counts)
        if tokens is None:
            return _plan("need_count", selected, inv.protected, summary, candidate, quality, None,
                "count_required" if quality == "unknown" else "advisory_count")
        if _fit(tokens, budget):
            return _plan("candidate", selected, inv.protected, summary, candidate, quality, None,
                         "within_budget" if number == 0 else "trimmed")
        demand = _new_demand(window, summary, budget.model_pin, budget.summarizer_context_digest,
                             limits, inv, tried)
        if demand is not None:
            return _plan("need_compaction", selected, inv.protected, summary,
                         candidate, quality, demand, "compaction_required")
        removable = next((g for g in selected if g.group_id not in inv.mandatory_groups), None)
        if removable is None:
            return _plan("cannot_fit", selected, inv.protected, summary,
                         candidate, quality, None, "mandatory_exceeds_budget")
        selected = tuple(g for g in selected if g.group_id != removable.group_id)
    return _plan("unavailable", selected, inv.protected, summary,
        _candidate(window, selected, summary, budget, limits, inv), "unknown", None, "candidate_limit")


def make_summary(demand, body):
    """Build untrusted result DATA, not a model call or durable publication.

    Provenance is a deterministic lineage commitment, not proof of genuine
    taint computation or export permission. B/C derives conservative union from
    actual journal/provenance and checks current policy.
    """
    record(demand, CompactionDemand)
    require(type(body) is bytes and len(body) <= demand.max_output_bytes, "bounds")
    key = demand.key
    data = dict(prefix_start=1, prefix_end=key.prefix_end, prefix_digest=key.prefix_digest,
        parent_summary_digest=key.parent_summary_digest, body=body,
        provenance_digest=summary_provenance(demand.consumed_provenance_digest, demand.parent_provenance_digest),
        policy_digest=key.policy_digest, summarizer_pin=key.summarizer_pin,
        attempt_key=key, coverage=demand.target_coverage,
        consumed_provenance_digest=demand.consumed_provenance_digest,
        parent_provenance_digest=demand.parent_provenance_digest,
        demand_input_digest=demand.input_digest, fingerprint="")
    temporary = object.__new__(SummaryRecord)
    for field, value in data.items():
        object.__setattr__(temporary, field, value)
    data["fingerprint"] = hash_data(summary_payload(temporary))
    return SummaryRecord(**data)


def check_summary_applicability(demand, result, current, current_summary):
    """Pure applicability/duplicate/collision; never a CAS write or fresh grant."""
    record(demand, CompactionDemand); record(result, SummaryRecord)
    if current_summary is not None:
        record(current_summary, SummaryRecord)
    # DATA shape plus result/demand reciprocity precedes historical dedup.
    require(result.attempt_key == demand.key and result.coverage == demand.target_coverage, "foreign")
    require(result.demand_input_digest == demand.input_digest)
    require(result.consumed_provenance_digest == demand.consumed_provenance_digest
        and result.parent_provenance_digest == demand.parent_provenance_digest)
    require(len(result.body) <= demand.max_output_bytes, "bounds")
    # Inspect current shape/bytes once; historical duplicate does not renew
    # authorization and may still acknowledge after policy/prefix revocation.
    from ._values import HistoryWindow
    record(current, HistoryWindow)
    require(identity(current) == identity(demand.key), "foreign")
    inv = _validated_inventory(current, MemoryLimits())
    if current_summary is not None and current_summary.attempt_key == demand.key:
        return ("duplicate" if canonical(summary_payload(current_summary)) == canonical(summary_payload(result))
                and current_summary.fingerprint == result.fingerprint else "collision")
    key = demand.key
    if (current.prefix_revision != key.prefix_revision or current.policy_digest != key.policy_digest
        or not _summary_matches(current, current_summary)
        or (None if current_summary is None else current_summary.fingerprint) != key.parent_summary_digest
        or key.prefix_end > current.head_sequence):
        return "stale"
    if (None if current_summary is None else current_summary.provenance_digest) != demand.parent_provenance_digest:
        return "stale"
    holes = {pin.group_id for pin in demand.target_coverage.retained_groups}
    try:
        target = _target(current, inv, key.prefix_end, holes)
        sources, consumed = _consumed(current, inv, key.prefix_end, holes)
    except MemoryError:
        return "stale"
    return ("applicable" if target == demand.target_coverage and sources == demand.source_keys
            and consumed == demand.consumed_provenance_digest else "stale")
