from dataclasses import replace
import pytest
from agent_native_memory import *
from support_memory import *


@pytest.mark.parametrize("quality,scope,tokens,status",[
    ("exact","full_wire",100,"candidate"),("upper_bound","full_wire",100,"candidate"),
    ("estimate","full_wire",100,"need_count"),("unknown","full_wire",None,"need_count"),
    ("exact","text_only",100,"need_count"),("upper_bound","text_only",100,"need_count")])
def test_only_complete_full_wire_count_can_plan_a_fit(quality,scope,tokens,status):
    w=window((singleton(1),singleton(2)))
    p=plan(w); count=counted(p,tokens,quality=quality,scope=scope)
    out=plan(w,counts=(count,))
    assert out.status==status
    assert not hasattr(out,"ready_to_send")


@pytest.mark.parametrize("field,value",[
    ("fixed_context_digest","f"*64),("summarizer_context_digest","f"*64),
    ("context_ceiling",900),("input_ceiling",700),("output_reservation",90),("safety_margin",11),
    ("model_pin",replace(PIN,model="different")),("model_pin",replace(PIN,account="different")),
    ("model_pin",replace(PIN,config_digest="f"*64)),("model_pin",replace(PIN,tokenizer_digest="f"*64)),
    ("model_pin",replace(PIN,serialization_digest="f"*64))])
def test_all_fixed_model_budget_identity_invalidates_previous_counts(field,value):
    w=window((singleton(1),singleton(2))); p=plan(w)
    out=plan(w,budget=replace(BUDGET,**{field:value}),counts=(counted(p,100),))
    assert out.status=="need_count" and out.candidate_digest!=p.candidate_digest


def test_instruction_frame_and_schema_manifests_are_not_just_history_hash():
    w=ancient(8); p=plan(w)
    for label in ("instructions","schema","frame","state","absent-frame"):
        import hashlib
        changed=replace(BUDGET,fixed_context_digest=hashlib.sha256(label.encode()).hexdigest())
        assert plan(w,budget=changed).candidate_digest!=p.candidate_digest


@pytest.mark.parametrize("quality",("exact","upper_bound"))
def test_full_wire_hard_evidence_missing_wire_is_shape_error(quality):
    with pytest.raises(MemoryError):
        CountEvidence("a"*64,PIN,quality,100,"full_wire",None)


@pytest.mark.parametrize("quality,tokens",(("unknown",0),("exact",None),("estimate",True),("exact",-1),("upper_bound",2**63)))
def test_count_exact_types_and_quality_invariants(quality,tokens):
    with pytest.raises(MemoryError):CountEvidence("a"*64,PIN,quality,tokens,"full_wire","e"*64)


def test_duplicate_counts_identical_ok_contradiction_not_smallest_wins():
    w=window((singleton(1),)); p=plan(w); c=counted(p,100)
    assert plan(w,counts=(c,c)).status=="candidate"
    with pytest.raises(MemoryError):plan(w,counts=(c,replace(c,input_tokens=101)))
    with pytest.raises(MemoryError):plan(w,counts=(c,replace(c,wire_digest="f"*64)))
    with pytest.raises(MemoryError):plan(w,counts=(c,replace(c,quality="upper_bound",input_tokens=99)))


@pytest.mark.parametrize("field",("context_ceiling","input_ceiling"))
def test_unknown_model_ceiling_is_honest_unavailable(field):
    w=window((singleton(1),)); assert plan(w,budget=replace(BUDGET,**{field:None})).reason=="unknown_ceiling"


def test_huge_mandatory_group_cannot_be_truncated_or_hidden_by_compaction():
    w=window((singleton(1),))
    p=plan(w)
    out=plan(w,counts=(counted(p,900),))
    assert out.status=="cannot_fit" and out.selected_group_ids==("g1",)
    assert out.demand is None and out.protected_source_keys==(w.latest_intent_key,)


def test_deterministic_trim_only_whole_groups_after_recorded_attempt():
    w=ancient(8); full=plan(w); d=demand(w)
    failed=(AttemptRecord(d.key,"failed"),)
    p=plan(w,counts=(counted(full,900),),attempts=failed)
    assert p.status=="need_count" and "ancient" in p.selected_group_ids
    assert len(p.selected_group_ids)==len(full.selected_group_ids)-1
    out=plan(w,counts=(counted(full,900),counted(p,100)),attempts=failed)
    assert out.status=="candidate" and out.reason=="trimmed"


def test_full_wire_usage_is_not_sum_of_message_counts():
    w=window((singleton(1),singleton(2))); p=plan(w)
    unrelated=CountEvidence("9"*64,PIN,"exact",1,"full_wire","e"*64)
    assert plan(w,counts=(unrelated,)).status=="need_count"
