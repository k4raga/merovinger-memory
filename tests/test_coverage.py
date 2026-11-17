from dataclasses import replace
import pytest
from agent_native_memory import *
from support_memory import *


def test_ancient_open_group_compacts_closed_suffix_and_keeps_exact_fact():
    w=ancient()
    validate_window(w,limits=LIMITS)
    p=plan(w)
    assert p.status=="need_count" and "ancient" in p.selected_group_ids
    assert {key.sequence for key in p.protected_source_keys}>={1,2,3,4,204}
    d=demand(w)
    assert d.key.prefix_start==1 and d.key.prefix_end>100
    assert d.target_coverage.retained_groups[0].group_id=="ancient"
    assert min(key.sequence for key in d.source_keys)==5
    assert all(key.sequence>4 for key in d.source_keys)
    s=make_summary(d,"Старые решения и ограничения 😀".encode())
    assert check_summary_applicability(d,s,w,None)=="applicable"
    newer=next_window(w,s)
    validate_window(newer,limits=LIMITS)
    assert len(newer.ordered_groups)<10
    assert newer.ordered_groups[0].entries==w.ordered_groups[0].entries
    assert plan(newer,summary=s).status=="need_count"


def test_later_closure_consumes_original_whole_hole_and_removes_it():
    original=ancient()
    first=demand(original)
    summary=make_summary(first,b"older closed decisions")
    w=next_window(original,summary)
    invocation=w.ordered_groups[0].entries[2]
    receipt=entry(w.head_sequence+1,"ancient","receipt",links=(invocation.source,))
    w2=next_window(w,summary,append=(receipt,),active=(),states={"ancient":"closed"})
    validate_window(w2,limits=LIMITS)
    second=demand(w2,summary=summary)
    assert [key.sequence for key in second.source_keys]==[1,2,3,4,205]
    assert "ancient" not in {pin.group_id for pin in second.target_coverage.retained_groups}
    assert second.parent_provenance_digest==summary.provenance_digest
    summary2=make_summary(second,b"cumulative decisions including resolved original operation")
    assert check_summary_applicability(second,summary2,w2,summary)=="applicable"
    last=next_window(w2,summary2,active=())
    validate_window(last,limits=LIMITS)
    assert "ancient" not in {g.group_id for g in last.ordered_groups}


def test_cross_turn_late_fact_in_retained_group_does_not_break_prefix_cas():
    w=ancient(12); d=demand(w); result=make_summary(d,b"summary")
    invocation=w.ordered_groups[0].entries[2]
    late=entry(w.head_sequence+1,"ancient","receipt",links=(invocation.source,))
    groups=(replace(w.ordered_groups[0],entries=w.ordered_groups[0].entries+(late,)),)+w.ordered_groups[1:]
    appended=window(groups,active=w.active_fact_keys)
    validate_window(appended,limits=LIMITS)
    assert check_summary_applicability(d,result,appended,None)=="applicable"
    assert plan(appended).candidate_digest!=plan(w).candidate_digest


@pytest.mark.parametrize("attack",("omitted_hole","extra_old","missing_tail","duplicate_hole","changed_hole","bad_order"))
def test_materialized_coverage_attacks_fail_closed(attack):
    original=ancient(12); s=make_summary(demand(original),b"summary")
    w=next_window(original,s)
    groups=w.ordered_groups
    if attack=="omitted_hole":
        bad=replace(w,ordered_groups=groups[1:])
    elif attack=="extra_old":
        bad=replace(w,ordered_groups=(groups[0],original.ordered_groups[1])+groups[1:])
    elif attack=="missing_tail":
        bad=replace(w,ordered_groups=groups[:-2]+groups[-1:])
    elif attack=="duplicate_hole":
        bad=replace(w,ordered_groups=(groups[0],)+groups)
    elif attack=="changed_hole":
        old=groups[0]
        changed=entry(4,"ancient","effect_fact",payload=b"changed",links=(old.entries[2].source,))
        bad=replace(w,ordered_groups=(replace(old,entries=old.entries[:-1]+(changed,)),)+groups[1:],
                    active_fact_keys=(changed.source,))
    else:
        bad=replace(w,ordered_groups=tuple(reversed(groups)))
    with pytest.raises(MemoryError):validate_window(bad,limits=LIMITS)
    assert plan(bad,summary=s).status=="unavailable"


def test_summary_covered_nonhole_reopened_requires_rebuild():
    a=entry(1,"executed"); b=entry(2,"executed","assistant_response",links=(a.source,))
    c=entry(3,"executed","invocation",links=(b.source,))
    r=entry(4,"executed","receipt",links=(c.source,))
    w=window((HistoryGroup("executed",(a,b,c,r),"closed"),)+tuple(singleton(i) for i in range(5,15)))
    s=make_summary(demand(w),b"summary")
    late=entry(15,"executed","effect_fact",links=(c.source,))
    newer=next_window(w,s,append=(late,),active=())
    assert plan(newer,summary=s).status=="unavailable"
    assert plan(newer,summary=s).reason=="requires_rebuild"


@pytest.mark.parametrize("attack",("crossgroup","dangling","missing_receipt","multi_invocation","forward","active_closed"))
def test_link_topology_cannot_fabricate_execution(attack):
    a=entry(1,"a"); b=entry(2,"a","assistant_response",links=(a.source,))
    c=entry(3,"a","invocation",links=(b.source,))
    r=entry(4,"a","receipt",links=(c.source,))
    group=HistoryGroup("a",(a,b,c,r),"closed")
    extra=singleton(5)
    active=()
    if attack=="crossgroup":
        group=replace(group,entries=(a,b,c,replace(r,links=(extra.entries[0].source,))))
    elif attack=="dangling":
        group=replace(group,entries=(a,b,c,replace(r,links=(entry(6,"missing").source,))))
    elif attack=="missing_receipt":
        group=replace(group,entries=(a,b,c))
        extra=singleton(4)
    elif attack=="multi_invocation":
        group=replace(group,entries=(a,b,c,entry(4,"a","invocation",links=(b.source,))))
    elif attack=="forward":
        group=replace(group,entries=(a,replace(b,links=(c.source,)),c,r))
    else:
        active=(c.source,)
    bad=window((group,extra),active=active)
    with pytest.raises(MemoryError):validate_window(bad,limits=LIMITS)


def test_multi_proposal_payload_does_not_become_an_invocation():
    a=entry(1,"a")
    response=entry(2,"a","assistant_response",payload=b'{"proposals":[1,2,3]}',links=(a.source,))
    w=window((HistoryGroup("a",(a,response),"closed"),singleton(3)))
    validate_window(w,limits=LIMITS)
    assert plan(w).status=="need_count"
