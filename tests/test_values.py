from dataclasses import replace,FrozenInstanceError
from hashlib import sha256
import pytest
from agent_native_memory import *
from support_memory import *


@pytest.mark.parametrize("bad",(True,-1,2**63,1.0,None))
def test_exact_unsigned_identity_numbers(bad):
    with pytest.raises(MemoryError):SourceKey("p","c",bad,"event",1,D)


@pytest.mark.parametrize("bad",("x"*(4*1024*1024),"é"*129,chr(0xD800),""))
def test_names_preflight_utf8_and_surrogate_caps(bad):
    import tracemalloc
    tracemalloc.start()
    try:
        with pytest.raises(MemoryError):ModelPin(bad,"m","a",D,None,D)
        _,peak=tracemalloc.get_traced_memory()
    finally:tracemalloc.stop()
    assert peak<64*1024


def test_nested_records_detached_immutable_bytes_shared_and_retag_denied():
    a=entry(1,"g")
    group=HistoryGroup("g",(a,),"closed")
    w=window((group,))
    assert w.ordered_groups[0].entries[0].payload is a.payload
    object.__setattr__(a.source,"event_id","changed")
    assert w.latest_intent_key.event_id=="event-1"
    with pytest.raises(FrozenInstanceError):w.head_sequence=20
    object.__setattr__(w.ordered_groups[0].entries[0],"payload",b"invalid retag")
    with pytest.raises(MemoryError):validate_window(w,limits=LIMITS)


@pytest.mark.parametrize("field,value",(("max_groups",257),("max_entries",2049),
    ("max_window_bytes",32*1024*1024+1),("max_summary_bytes",16385),
    ("max_source_refs",2049),("max_candidates",258),("keep_recent_groups",True)))
def test_limits_cannot_silently_expand_named_profile(field,value):
    with pytest.raises(MemoryError):MemoryLimits(**{field:value})


def test_custom_bounded_window_limits_return_unavailable_without_trimming():
    w=window(tuple(singleton(i) for i in range(1,5)))
    limits=MemoryLimits("small",2,2048,32*1024*1024,16384,2048,1,3)
    out=plan(w,limits=limits)
    assert out.status=="unavailable" and out.reason=="window_unavailable"
    assert out.selected_group_ids==()


def test_payload_and_source_reference_caps_are_whole_window_bounds():
    a=entry(1,"a",payload=b"x"*512)
    b=entry(2,"b",payload=b"y"*512)
    w=window((HistoryGroup("a",(a,),"closed"),HistoryGroup("b",(b,),"closed")))
    small=MemoryLimits("small",256,2048,1023,16384,2048,4,257)
    assert plan(w,limits=small).status=="unavailable"
    with pytest.raises(MemoryError):HistoryEntry(a.source,"a","user_intent",bytearray(a.payload),(),D)


def test_summary_byte_escape_and_utf8_limits_and_parent_reciprocity():
    w=ancient(8); d=demand(w)
    with pytest.raises(MemoryError):make_summary(d,b"x"*16385)
    with pytest.raises(MemoryError):make_summary(d,b"\xff")
    with pytest.raises(MemoryError):make_summary(d,b"\x00"*6000)
    with pytest.raises(MemoryError):replace(d,parent_provenance_digest=D)
    s=make_summary(d,"Русский 😀 code\nprint('data')".encode())
    assert s.body.decode().startswith("Русский")


def test_budget_checked_arithmetic_no_bool_or_unknown_substitution():
    with pytest.raises(MemoryError):replace(BUDGET,output_reservation=2**63-1,safety_margin=1)
    with pytest.raises(MemoryError):replace(BUDGET,input_ceiling=True)
    with pytest.raises(MemoryError):replace(BUDGET,fixed_context_digest=None)


def test_metadata_only_clone_is_not_original_authority_and_is_revalidated():
    w=ancient(8); d=demand(w); s=make_summary(d,b"data")
    cloned=replace(s)
    assert cloned==s and check_summary_applicability(d,cloned,w,None)=="applicable"
    # This is accepted DATA equality, never an issued handle or SEND authority.
    assert not hasattr(cloned,"grant") and not hasattr(cloned,"ready_to_send")


def test_group_entry_and_ref_ceilings_before_unbounded_work():
    with pytest.raises(MemoryError):
        window(tuple(singleton(i) for i in range(1,258)))
    one=entry(1,"many")
    with pytest.raises(MemoryError):HistoryGroup("many",(one,)*2049,"closed")
    with pytest.raises(MemoryError):replace(one,links=(one.source,)*2049)


def test_global_large_horizon_uses_only_bounded_materialized_inventory():
    from agent_native_memory._window import _inspect,_retained_pin
    from agent_native_memory._values import coverage_digest,demand_input
    import time
    old=singleton(1); inv=_inspect(window((old,)),LIMITS); horizon=10**12
    pin=_retained_pin(old,horizon,inv.entry_digests)
    coverage=CoverageManifest(horizon,D,(pin,))
    key=AttemptKey("project","conversation",1,1,1,horizon,D,None,D,PIN,"d"*64,coverage_digest(coverage))
    source=entry(2,"already-summarized").source
    demand_data=CompactionDemand(key,(source,),coverage,D,None,
        demand_input(key,coverage,(source,),D,None,16384),16384)
    base=make_summary(demand_data,b"DATA only; journal truth is future B")
    w=window((old,singleton(horizon+1)),base=base,head=horizon+1)
    start=time.monotonic()
    validate_window(w,limits=LIMITS)
    assert plan(w,summary=base).status=="need_count"
    assert time.monotonic()-start<1


def test_full_32mib_window_hashes_payload_once_per_planner_call(monkeypatch):
    import agent_native_memory._values as values
    payload1=b"x"*(16*1024*1024); payload2=b"y"*(16*1024*1024)
    a=entry(1,"a",payload=payload1); b=entry(2,"b",payload=payload2)
    w=window((HistoryGroup("a",(a,),"closed"),HistoryGroup("b",(b,),"closed")))
    original=values.sha256; calls={id(payload1):0,id(payload2):0}
    def measured(value=b""):
        if id(value) in calls:calls[id(value)]+=1
        return original(value)
    monkeypatch.setattr(values,"sha256",measured)
    assert plan(w).status=="need_count"
    assert list(calls.values())==[1,1]
    assert w.ordered_groups[0].entries[0].payload is payload1

def test_aggregate_source_refs_reject_before_payload_rehash(monkeypatch):
    import agent_native_memory._values as values
    a=entry(1,"a")
    linked=entry(2,"a","assistant_response",links=(a.source,)*1100)
    other=entry(3,"a","assistant_response",links=(a.source,)*1100)
    original=values.sha256
    calls=0
    def measured(data=b""):
        nonlocal calls
        if data is a.payload or data is linked.payload or data is other.payload:calls+=1
        return original(data)
    monkeypatch.setattr(values,"sha256",measured)
    with pytest.raises(MemoryError):HistoryGroup("a",(a,linked,other),"closed")
    assert calls==0

def test_empty_tail_requires_horizon_head_and_materialized_latest_intent():
    w=ancient(8); d=demand(w); summary=make_summary(d,b"summary")
    newer=next_window(w,summary)
    with pytest.raises(MemoryError):
        validate_window(replace(newer,head_sequence=summary.coverage.horizon-1),limits=LIMITS)

def test_small_candidate_budget_is_finite_not_unbounded_retry():
    w=ancient(8); d=demand(w); p=plan(w)
    limited=MemoryLimits("one-decision",256,2048,32*1024*1024,16384,2048,4,1)
    start=plan(w,limits=limited)
    result=plan(w,counts=(counted(start,900),),attempts=(AttemptRecord(d.key,"failed"),),limits=limited)
    assert result.status=="unavailable" and result.reason=="candidate_limit"

def test_imports_are_pure_no_worker_backend_or_callbacks():
    import subprocess,sys
    script="import sys; import agent_native_memory; assert not any(m.startswith(('agent_native.kernel','agent_native.observation','PySide6','wasmtime','agent_native.programming.backend')) for m in sys.modules)"
    subprocess.run([sys.executable,"-c",script],check=True,timeout=5)

def test_no_demand_invokes_no_model_and_public_module_is_only_pure_functions():
    import ast
    from pathlib import Path
    import agent_native_memory
    root=Path(agent_native_memory.__file__).parent
    forbidden={"exec","eval","compile","open","__import__"}
    for p in (root/name for name in ("_values.py", "_window.py", "_planner.py")):
        tree=ast.parse(p.read_text())
        assert not any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name)
            and n.func.id in forbidden for n in ast.walk(tree))
    assert plan(window((singleton(1),))).status=="need_count"

def test_maximum_groups_and_entries_refs_are_valid_finite_windows():
    groups=window(tuple(singleton(i) for i in range(1,257)))
    validate_window(groups,limits=LIMITS)
    assert len(plan(groups).selected_group_ids)==256
    a=entry(1,"max"); b=entry(2,"max","assistant_response",links=(a.source,))
    invocation=entry(3,"max","invocation",links=(b.source,))
    receipts=tuple(entry(i,"max","receipt",links=(invocation.source,)) for i in range(4,2049))
    maximum=window((HistoryGroup("max",(a,b,invocation)+receipts,"closed"),))
    validate_window(maximum,limits=LIMITS)
    assert len(maximum.ordered_groups[0].entries)==2048
    assert sum(len(e.links) for e in maximum.ordered_groups[0].entries)+1==2048

def test_arbitrary_object_equality_and_string_subclass_hooks_never_invoked():
    calls=[]
    class Meta(type):
        def __eq__(self,other):
            calls.append("type equality")
            raise AssertionError("must not invoke user code")
    class Object(metaclass=Meta):
        pass
    class Text(str):
        def encode(self,*args,**kwargs):
            calls.append("encode")
            raise AssertionError("must not invoke user code")
    with pytest.raises(MemoryError):
        plan(Object())
    with pytest.raises(MemoryError):
        ModelPin(Text("provider"),"model","account",D,None,D)
    assert calls==[]

def test_counts_and_attempt_input_tuples_have_finite_caps():
    w=window((singleton(1),)); p=plan(w); c=counted(p,100)
    with pytest.raises(MemoryError):plan(w,counts=(c,)*258)

def test_applicability_payload_validation_is_one_pass(monkeypatch):
    import agent_native_memory._values as values
    w=ancient(12); d=demand(w); result=make_summary(d,b"summary")
    payload_ids={id(e.payload):0 for g in w.ordered_groups for e in g.entries}
    original=values.sha256
    def measured(data=b""):
        if id(data) in payload_ids:payload_ids[id(data)]+=1
        return original(data)
    monkeypatch.setattr(values,"sha256",measured)
    assert check_summary_applicability(d,result,w,None)=="applicable"
    assert set(payload_ids.values())=={1}
