from dataclasses import replace
from hashlib import sha256
from agent_native_memory import *

D="a"*64
PIN=ModelPin("fixture","model","account",D,None,"b"*64)
BUDGET=ContextBudget(PIN,"c"*64,"d"*64,1000,800,100,10)
LIMITS=MemoryLimits()


def entry(seq, group, kind="user_intent", *, payload=None, links=(), provenance=D):
    payload=("Событие "+str(seq)+" 😀\n"+'x = {"пример": True}').encode() if payload is None else payload
    key=SourceKey("project","conversation",1,"event-"+str(seq),seq,sha256(payload).hexdigest())
    return HistoryEntry(key,group,kind,payload,tuple(links),provenance)


def singleton(seq):
    return HistoryGroup("g"+str(seq),(entry(seq,"g"+str(seq)),),"closed")


def ancient(n=201):
    a=entry(1,"ancient")
    b=entry(2,"ancient","assistant_response",links=(a.source,))
    c=entry(3,"ancient","invocation",links=(b.source,))
    d=entry(4,"ancient","effect_fact",links=(c.source,))
    groups=(HistoryGroup("ancient",(a,b,c,d),"open"),)+tuple(singleton(seq) for seq in range(5,n+4))
    return window(groups, active=(d.source,))


def window(groups, *, base=None, active=(), revision=1, policy=D, head=None):
    groups=tuple(sorted(groups,key=lambda g:g.entries[0].source.sequence))
    entries=tuple(e for g in groups for e in g.entries)
    latest=max((e.source for e in entries if e.kind=="user_intent"),key=lambda key:key.sequence)
    return HistoryWindow("project","conversation",1,revision,1 if base is None else base.coverage.horizon+1,
        groups,latest,max(max(e.source.sequence for e in entries),0 if base is None else base.coverage.horizon) if head is None else head,
        tuple(active),policy,None if base is None else base.coverage,
        None if base is None else base.fingerprint)


def plan(w, *, summary=None,budget=BUDGET,counts=(),attempts=(),limits=LIMITS):
    return plan_context(w,summary=summary,budget=budget,counts=tuple(counts),
        attempts=tuple(attempts),limits=limits)


def counted(p,tokens,*,quality="exact",scope="full_wire",pin=PIN,wire="e"*64):
    return CountEvidence(p.candidate_digest,pin,quality,tokens,scope,
        None if scope=="text_only" or quality=="unknown" else wire)


def demand(w, *, summary=None,budget=BUDGET):
    p=plan(w,summary=summary,budget=budget)
    result=plan(w,summary=summary,budget=budget,counts=(counted(p,900,pin=budget.model_pin),))
    assert result.status=="need_compaction"
    return result.demand


def next_window(w,summary,*,append=(),active=None,states=None):
    coverage=summary.coverage
    holes={pin.group_id for pin in coverage.retained_groups}
    mapping={g.group_id:(list(g.entries),g.state) for g in w.ordered_groups}
    for e in append:
        mapping.setdefault(e.group_id,([], "closed"))[0].append(e)
    groups=[]
    for ident,(entries,state) in mapping.items():
        kept=tuple(sorted((e for e in entries if e.source.sequence>coverage.horizon or ident in holes),
            key=lambda e:e.source.sequence))
        if kept:
            groups.append(HistoryGroup(ident,kept,(states or {}).get(ident,state)))
    return window(groups,base=summary,active=w.active_fact_keys if active is None else active)
