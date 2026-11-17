"""Bounded materialized coverage, closure and metadata commitments.

Labels are DATA. Actual journal coverage/accepted base/provenance/export
authorization are independently checked by future B/C producers.
"""
from dataclasses import dataclass
from ._values import (
    MemoryError, MemoryLimits, HistoryWindow, require, record, plain, hash_data,
    identity, RetainedGroupPin, CoverageManifest, coverage_digest,
)


@dataclass(frozen=True, slots=True)
class _Inventory:
    by_key: dict
    entries: tuple
    protected: tuple
    mandatory_groups: frozenset
    entry_digests: dict
    group_digests: dict


def _entry_manifest(entry):
    return {name: plain(getattr(entry, name)) for name in
            ("source", "group_id", "kind", "links", "provenance_digest")}


def _retained_pin(group, horizon, entry_digests):
    subset = tuple(e for e in group.entries if e.source.sequence <= horizon)
    require(bool(subset), "coverage")
    return RetainedGroupPin(group.group_id, subset[0].source.sequence, len(subset),
        hash_data({"format": "memory-retained/1",
                   "entries": [entry_digests[e.source] for e in subset]}))


def _inspect(window, limits):
    record(limits, MemoryLimits); record(window, HistoryWindow)
    return _validated_inventory(window, limits)


def _validated_inventory(window, limits):
    groups = window.ordered_groups
    require(len(groups) <= limits.max_groups, "bounds")
    require(len({g.group_id for g in groups}) == len(groups), "coverage")
    entries = tuple(sorted((e for g in groups for e in g.entries), key=lambda e: e.source.sequence))
    require(len(entries) <= limits.max_entries
        and sum(len(e.payload) for e in entries) <= limits.max_window_bytes, "bounds")
    require(sum(len(e.links) for e in entries) + len(window.active_fact_keys) + 1
        <= limits.max_source_refs, "bounds")
    require(all(identity(e.source) == identity(window) for e in entries), "foreign")
    seqs = [e.source.sequence for e in entries]
    require(len(set(seqs)) == len(seqs)
        and len({e.source.event_id for e in entries}) == len(entries), "coverage")
    by_key = {e.source: e for e in entries}
    digests = {e.source: hash_data({"format": "memory-entry/1", **_entry_manifest(e)}) for e in entries}
    base = window.base_coverage
    horizon = 0 if base is None else base.horizon
    require(window.coverage_start == horizon + 1 and window.head_sequence >= horizon, "coverage")
    pins = {} if base is None else {p.group_id: p for p in base.retained_groups}
    group_by_id = {g.group_id: g for g in groups}
    old = [e for e in entries if e.source.sequence <= horizon]
    require(all(e.group_id in pins for e in old), "coverage")
    for ident, pin in pins.items():
        require(ident in group_by_id, "coverage")
        require(_retained_pin(group_by_id[ident], horizon, digests) == pin, "coverage")
    tail = [e.source.sequence for e in entries if e.source.sequence > horizon]
    if tail:
        require(tail[0] == window.coverage_start and tail[-1] == window.head_sequence
            and all(b == a + 1 for a, b in zip(tail, tail[1:])), "coverage")
    else:
        require(window.head_sequence == horizon, "coverage")
    firsts = []
    for group in groups:
        local = [e.source.sequence for e in group.entries]
        require(local == sorted(local), "coverage")
        firsts.append(local[0])
        require(all(e.group_id == group.group_id for e in group.entries), "pairing")
        receipt_for, used_responses, invocations = set(), set(), []
        for entry in group.entries:
            require(len(set(entry.links)) == len(entry.links), "pairing")
            for link in entry.links:
                require(identity(link) == identity(window), "foreign")
                if link not in by_key and horizon and link.sequence <= horizon:
                    # Cannot invent a pair for a summary-covered non-hole.
                    raise MemoryError("stale")
                require(link in by_key and link.sequence < entry.source.sequence
                    and by_key[link].group_id == group.group_id, "pairing")
            kinds = [by_key[k].kind for k in entry.links]
            if entry.kind == "user_intent":
                require(not kinds, "pairing")
            elif entry.kind == "assistant_response":
                require(not kinds or kinds == ["user_intent"], "pairing")
            elif entry.kind == "invocation":
                require(kinds == ["assistant_response"] and entry.links[0] not in used_responses, "pairing")
                invocations.append(entry.source); used_responses.add(entry.links[0])
            elif entry.kind in ("receipt", "effect_fact"):
                require(kinds == ["invocation"], "pairing")
                if entry.kind == "receipt":
                    receipt_for.add(entry.links[0])
            else:
                require(len(kinds) == 1, "pairing")
        if group.state == "closed":
            require(all(key in receipt_for for key in invocations), "pairing")
    require(firsts == sorted(firsts), "coverage")
    latest = window.latest_intent_key
    require(latest in by_key and by_key[latest].kind == "user_intent", "pairing")
    require(latest.sequence == max(e.source.sequence for e in entries if e.kind == "user_intent"), "pairing")
    require(len(set(window.active_fact_keys)) == len(window.active_fact_keys), "pairing")
    for key in window.active_fact_keys:
        require(key in by_key and by_key[key].kind in ("invocation", "receipt", "effect_fact"), "pairing")
        require(group_by_id[by_key[key].group_id].state == "open", "pairing")
    protected = {latest, *window.active_fact_keys}
    for group in groups:
        if group.state == "open":
            protected.update(e.source for e in group.entries)
    protected = tuple(sorted(protected, key=lambda key: key.sequence))
    require(len(protected) <= limits.max_source_refs, "bounds")
    group_digests = {g.group_id: hash_data({"format": "memory-group/1", "group_id": g.group_id,
        "state": g.state, "entries": [digests[e.source] for e in g.entries]}) for g in groups}
    return _Inventory(by_key, entries, protected,
        frozenset(by_key[k].group_id for k in protected), digests, group_digests)


def validate_window(window, *, limits):
    """Validate a finite raw segment+retained holes; no journal attestation."""
    _inspect(window, limits)


def _prefix_digest(window, inv, horizon):
    base = window.base_coverage
    old = 0 if base is None else base.horizon
    require(old <= horizon <= window.head_sequence and horizon >= 1, "coverage")
    if horizon == old:
        return base.prefix_digest
    tail = [inv.entry_digests[e.source] for e in inv.entries if old < e.source.sequence <= horizon]
    if base is None:
        return hash_data({"format": "memory-prefix/1", "inventory": tail})
    return hash_data({"format": "memory-prefix-continuation/1",
        "base_prefix": base.prefix_digest, "base_horizon": old, "tail": tail})


def _target(window, inv, horizon, holes):
    """Reconstruct a pinned fence without scanning the global head range."""
    pins = tuple(sorted((_retained_pin(g, horizon, inv.entry_digests)
        for g in window.ordered_groups if g.group_id in holes
        and any(e.source.sequence <= horizon for e in g.entries)),
        key=lambda pin: (pin.first_sequence, pin.group_id)))
    return CoverageManifest(horizon, _prefix_digest(window, inv, horizon), pins)


def _consumed(window, inv, horizon, holes):
    groups = tuple(g for g in window.ordered_groups
        if g.group_id not in holes and any(e.source.sequence <= horizon for e in g.entries))
    require(all(g.state == "closed" and g.group_id not in inv.mandatory_groups
        and all(e.source.sequence <= horizon for e in g.entries) for g in groups), "stale")
    entries = tuple(sorted((e for g in groups for e in g.entries), key=lambda e: e.source.sequence))
    provenance = hash_data({"format": "memory-consumed-provenance/1",
        "entries": [inv.entry_digests[e.source] for e in entries]})
    return tuple(e.source for e in entries), provenance


def _summary_matches(window, summary):
    if summary is None:
        return window.base_coverage is None
    key = summary.attempt_key
    return (identity(key) == identity(window) and key.prefix_revision == window.prefix_revision
        and key.policy_digest == window.policy_digest
        and window.base_summary_fingerprint == summary.fingerprint
        and window.base_coverage == summary.coverage)
