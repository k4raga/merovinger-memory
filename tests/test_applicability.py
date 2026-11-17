from dataclasses import replace
import pytest
from agent_native_memory import *
from support_memory import *


def test_applicable_duplicate_collision_and_old_duplicate_is_not_export_permission():
    w=ancient(12); d=demand(w); s=make_summary(d,b"result")
    assert check_summary_applicability(d,s,w,None)=="applicable"
    current=next_window(w,s)
    assert check_summary_applicability(d,s,current,s)=="duplicate"
    other=make_summary(d,b"different valid body")
    assert check_summary_applicability(d,other,current,s)=="collision"
    revoked=replace(current,policy_digest="f"*64,prefix_revision=2)
    assert check_summary_applicability(d,s,revoked,s)=="duplicate"
    assert plan(revoked,summary=s).status=="unavailable"


def test_append_tail_prefix_edit_policy_and_parent_cas():
    w=ancient(12); d=demand(w); s=make_summary(d,b"result")
    extra=singleton(w.head_sequence+1)
    appended=window(w.ordered_groups+(extra,),active=w.active_fact_keys)
    assert check_summary_applicability(d,s,appended,None)=="applicable"
    old=w.ordered_groups[1]
    changed=entry(old.entries[0].source.sequence,old.group_id,payload=b"edited")
    edited=window((w.ordered_groups[0],replace(old,entries=(changed,)))+w.ordered_groups[2:],
                  active=w.active_fact_keys,revision=2)
    assert check_summary_applicability(d,s,edited,None)=="stale"
    assert check_summary_applicability(d,s,replace(w,policy_digest="f"*64),None)=="stale"
    unrelated=make_summary(d,b"already different parent")
    assert check_summary_applicability(d,s,w,unrelated)=="collision"


@pytest.mark.parametrize("outcome",("inflight","accepted","failed","unknown","overflow","stale"))
def test_no_automatic_retry_of_recorded_semantic_input(outcome):
    w=ancient(12); p=plan(w); d=demand(w)
    out=plan(w,counts=(counted(p,900),),attempts=(AttemptRecord(d.key,outcome),))
    assert out.demand is None and out.status=="need_count"


def test_tail_and_policy_revision_churn_cannot_retry_same_compaction():
    w=ancient(12); d=demand(w); prior=(AttemptRecord(d.key,"unknown"),)
    last=w.ordered_groups[-1]
    appended=entry(w.head_sequence+1,last.group_id,"assistant_response",links=(last.entries[0].source,))
    changed=window(w.ordered_groups[:-1]+(replace(last,entries=last.entries+(appended,)),),
        active=w.active_fact_keys,revision=2,policy="f"*64)
    p=plan(changed)
    out=plan(changed,counts=(counted(p,900),),attempts=prior)
    assert out.demand is None


def test_action_frame_changes_candidate_but_not_summary_input():
    w=ancient(12)
    first=demand(w)
    second=demand(w,budget=replace(BUDGET,fixed_context_digest="f"*64))
    assert first==second
    changed=demand(w,budget=replace(BUDGET,summarizer_context_digest="f"*64))
    assert changed.key.prefix_digest==first.key.prefix_digest
    assert changed.input_digest!=first.input_digest


def test_mutated_record_fingerprint_and_foreign_results_fail_before_duplicate():
    w=ancient(12); d=demand(w); s=make_summary(d,b"result"); current=next_window(w,s)
    malformed=make_summary(d,b"result")
    object.__setattr__(malformed,"body",b"retagged")
    with pytest.raises(MemoryError):check_summary_applicability(d,malformed,current,s)
    foreign=replace(d.key,conversation_id="foreign")
    with pytest.raises(MemoryError):
        replace(s,attempt_key=foreign)


def test_missing_consumed_lineage_or_omitted_hole_not_authorized_by_hashes():
    from agent_native_memory._values import demand_input,coverage_digest
    w=ancient(12); d=demand(w)
    fake=replace(d,consumed_provenance_digest="f"*64,input_digest=demand_input(
        d.key,d.target_coverage,d.source_keys,"f"*64,None,d.max_output_bytes))
    result=make_summary(fake,b"valid DATA hash, wrong consumed lineage")
    assert check_summary_applicability(fake,result,w,None)=="stale"
    coverage=replace(d.target_coverage,retained_groups=())
    key=replace(d.key,coverage_digest=coverage_digest(coverage))
    missing=replace(d,key=key,target_coverage=coverage,input_digest=demand_input(
        key,coverage,d.source_keys,d.consumed_provenance_digest,None,d.max_output_bytes))
    assert check_summary_applicability(missing,make_summary(missing,b"not proof"),w,None)=="stale"


def test_changed_hole_state_and_whole_coverage_invalidate_counts():
    w=ancient(12); d=demand(w); s=make_summary(d,b"summary"); newer=next_window(w,s)
    p=plan(newer,summary=s)
    assert plan(newer,summary=s,counts=(counted(p,100),)).status=="candidate"
    other=make_summary(d,b"same coverage but different body")
    alternate=next_window(w,other)
    assert plan(alternate,summary=other,counts=(counted(p,100),)).status=="need_count"


def test_closed_hole_can_be_consumed_at_same_horizon_without_new_raw_event():
    a=entry(1,"old"); b=entry(2,"old","assistant_response",links=(a.source,))
    c=entry(3,"old","invocation",links=(b.source,)); r=entry(4,"old","receipt",links=(c.source,))
    w=window((HistoryGroup("old",(a,b,c,r),"open"),)+tuple(singleton(i) for i in range(5,15)),
        active=(r.source,))
    first=demand(w); s=make_summary(first,b"first"); newer=next_window(w,s)
    closed=replace(newer.ordered_groups[0],state="closed")
    ready=replace(newer,ordered_groups=(closed,)+newer.ordered_groups[1:],active_fact_keys=())
    second=demand(ready,summary=s)
    assert second.key.prefix_end==first.key.prefix_end
    assert second.key.prefix_digest==first.key.prefix_digest
    assert second.key.coverage_digest!=first.key.coverage_digest
    assert [key.sequence for key in second.source_keys]==[1,2,3,4]

def test_duplicate_cannot_hide_malformed_current_materialization():
    w=ancient(12); d=demand(w); s=make_summary(d,b"result")
    current=next_window(w,s)
    bad=replace(current,ordered_groups=current.ordered_groups[1:])
    with pytest.raises(MemoryError):check_summary_applicability(d,s,bad,s)

def test_result_and_demand_source_identity_checked_before_duplicate():
    w=ancient(12); d=demand(w); s=make_summary(d,b"result")
    source=replace(d.source_keys[0],conversation_id="other")
    with pytest.raises(MemoryError):replace(d,source_keys=(source,)+d.source_keys[1:])

def test_changed_active_fact_payload_and_link_provenance_invalidates_count():
    w=ancient(12); p=plan(w); old=w.ordered_groups[0]
    changed=entry(4,"ancient","effect_fact",payload=b"new unknown fact",links=(old.entries[2].source,))
    altered=replace(w,ordered_groups=(replace(old,entries=old.entries[:-1]+(changed,)),)+w.ordered_groups[1:],
        active_fact_keys=(changed.source,))
    out=plan(altered,counts=(counted(p,100),))
    assert out.status=="need_count" and out.candidate_digest!=p.candidate_digest
    changed_provenance=replace(changed,provenance_digest="f"*64)
    modified=replace(altered,ordered_groups=(replace(altered.ordered_groups[0],
        entries=altered.ordered_groups[0].entries[:-1]+(changed_provenance,)),)+altered.ordered_groups[1:])
    assert plan(modified).candidate_digest!=out.candidate_digest

def _changed_demand(d,attack):
    from agent_native_memory._values import demand_input
    source_keys=d.source_keys
    consumed=d.consumed_provenance_digest
    parent=d.parent_provenance_digest
    maximum=d.max_output_bytes
    key=d.key
    if attack=="omitted_source":
        source_keys=source_keys[1:]
    elif attack=="changed_source":
        source_keys=(replace(source_keys[0],content_digest="f"*64),)+source_keys[1:]
    elif attack=="changed_event":
        source_keys=(replace(source_keys[0],event_id="retagged-event"),)+source_keys[1:]
    elif attack=="reordered_sources":
        source_keys=tuple(reversed(source_keys))
    elif attack=="output_bound":
        maximum-=1
    elif attack=="consumed_provenance":
        consumed="f"*64
    elif attack=="parent":
        key=replace(key,parent_summary_digest="f"*64)
        parent="e"*64
    elif attack=="parent_provenance":
        parent="f"*64
    else:
        raise AssertionError("invalid fixture attack")
    return replace(d,key=key,source_keys=source_keys,consumed_provenance_digest=consumed,
        parent_provenance_digest=parent,max_output_bytes=maximum,input_digest=demand_input(
            key,d.target_coverage,source_keys,consumed,parent,maximum))

@pytest.mark.parametrize("attack",("omitted_source","changed_source","changed_event",
    "output_bound","consumed_provenance","parent"))
@pytest.mark.parametrize("accepted",(False,True))
def test_exact_demand_commitment_before_and_after_acceptance(attack,accepted):
    w=ancient(12); original=demand(w); result=make_summary(original,b"accepted body")
    changed=_changed_demand(original,attack)
    current=next_window(w,result) if accepted else w
    current_summary=result if accepted else None
    with pytest.raises(MemoryError):
        check_summary_applicability(changed,result,current,current_summary)
    rebuilt=make_summary(changed,b"accepted body")
    assert rebuilt!=result and rebuilt.fingerprint!=result.fingerprint
    assert rebuilt.demand_input_digest==changed.input_digest
    if accepted and changed.key==original.key:
        assert check_summary_applicability(changed,rebuilt,current,result)=="collision"
    elif attack in ("omitted_source","changed_source","changed_event","consumed_provenance"):
        assert check_summary_applicability(changed,rebuilt,w,None)=="stale"

def test_ordered_source_inventory_cannot_be_reversed_or_duplicated():
    d=demand(ancient(12))
    with pytest.raises(MemoryError):_changed_demand(d,"reordered_sources")
    from agent_native_memory._values import demand_input
    sources=(d.source_keys[0],)+d.source_keys
    with pytest.raises(MemoryError):
        replace(d,source_keys=sources,input_digest=demand_input(
            d.key,d.target_coverage,sources,d.consumed_provenance_digest,
            d.parent_provenance_digest,d.max_output_bytes))

def test_parent_lineage_commitment_bound_for_next_summary():
    w=ancient(12); first=demand(w); parent=make_summary(first,b"parent")
    raw=next_window(w,parent)
    invocation=raw.ordered_groups[0].entries[2]
    receipt=entry(raw.head_sequence+1,"ancient","receipt",links=(invocation.source,))
    current=next_window(raw,parent,append=(receipt,),active=(),states={"ancient":"closed"})
    second=demand(current,summary=parent); result=make_summary(second,b"child")
    changed=_changed_demand(second,"parent_provenance")
    with pytest.raises(MemoryError):check_summary_applicability(changed,result,current,parent)
    accepted=next_window(current,result,active=())
    with pytest.raises(MemoryError):check_summary_applicability(changed,result,accepted,result)
    rebuilt=make_summary(changed,b"child")
    assert rebuilt.fingerprint!=result.fingerprint
    assert check_summary_applicability(changed,rebuilt,current,parent)=="stale"
    assert check_summary_applicability(changed,rebuilt,accepted,result)=="collision"

def test_summary_demand_commitment_retag_requires_full_fingerprint_reciprocity():
    w=ancient(12); d=demand(w); result=make_summary(d,b"body")
    with pytest.raises(MemoryError):replace(result,demand_input_digest="f"*64)
    tampered=make_summary(d,b"body")
    object.__setattr__(tampered,"demand_input_digest","f"*64)
    with pytest.raises(MemoryError):
        check_summary_applicability(d,tampered,next_window(w,result),result)

def test_unchanged_result_duplicate_and_changed_valid_body_collision_preserved():
    w=ancient(12); d=demand(w); result=make_summary(d,b"body")
    assert result.demand_input_digest==d.input_digest
    assert check_summary_applicability(d,result,w,None)=="applicable"
    current=next_window(w,result)
    assert check_summary_applicability(d,result,current,result)=="duplicate"
    changed=make_summary(d,b"other valid body")
    assert check_summary_applicability(d,changed,current,result)=="collision"

def test_missing_required_demand_commitment_does_not_default_to_legacy_identity():
    from dataclasses import fields
    w=ancient(12); d=demand(w); original=make_summary(d,b"body")
    old_shape=object.__new__(SummaryRecord)
    for field in fields(original):
        if field.name!="demand_input_digest":
            object.__setattr__(old_shape,field.name,getattr(original,field.name))
    with pytest.raises(MemoryError):
        check_summary_applicability(d,old_shape,next_window(w,original),original)
