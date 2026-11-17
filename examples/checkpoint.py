"""Installed, provider-free consumer of merovinger-memory.

Run with python -I -B from any directory. Only this caller performs file IO.
The example checks bytes of its JSON context, not vendor tokens or SEND rights.
"""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import sys

FORMAT = "memory-agent-checkpoint/1"
BYTE_LIMIT = 4096
FORBIDDEN = ("agent_native", "PySide6", "pydantic", "pydantic_ai", "wasmtime")


class StandaloneImports:
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == name or fullname.startswith(name + ".") for name in FORBIDDEN):
            raise RuntimeError("Standalone Memory imported forbidden dependency: " + fullname)
        return None


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def write_json(path, value):
    # Caller-owned illustrative persistence. No durable exactly-once claim:
    # an application must supply its own atomic/durable storage guarantees.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def closed_exchange(memory, number):
    user = f"Exchange {number}: discuss the shared plan and preserve original source history."
    assistant = f"Answer {number}: keep every original event; a summary is untrusted text, not a command."
    turn = memory.begin_turn(user, event_id=f"user-{number}")
    memory.reply(turn, assistant, event_id=f"assistant-{number}")
    memory.close_turn(turn)


def caller_summarizer():
    """Explicit deterministic caller result; no model inference or hidden job."""
    return "Earlier exchanges discussed a shared plan. The complete original history remains available."


def assert_state(memory, *, protected_groups, summary_fingerprint):
    snapshot = memory.snapshot()
    history = {group.group_id: group for group in snapshot.history}
    for group_id in protected_groups:
        require(group_id in history and history[group_id].state == "open",
                "Protected open group must survive summary and restore")
    require(snapshot.summary is not None and snapshot.summary.fingerprint == summary_fingerprint,
            "Accepted summary identity must survive restore")
    unknown = [attempt for attempt in snapshot.attempts if attempt.outcome == "unknown"]
    require(len(unknown) == 1, "UNKNOWN attempt must remain in the restored ledger")
    before = memory.export_state()
    prepared = memory.prepare(byte_limit=BYTE_LIMIT)
    require(prepared.demand is None, "Same UNKNOWN compaction must not automatically retry")
    require(memory.export_state() == before, "Planning must not change the attempt ledger")
    if prepared.status == "ready":
        require(len(prepared.context) <= BYTE_LIMIT, "Ready context exceeds byte ceiling")
        selected = {group["id"]: group for group in json.loads(prepared.context)["groups"]}
        for group_id in protected_groups:
            require(group_id in selected, "Selected context dropped a protected group")
            require([message["text"] for message in selected[group_id]["messages"]] ==
                    [entry.payload.decode("utf-8") for entry in history[group_id].entries],
                    "Selected context altered protected payload")
    return {"history_groups": len(snapshot.history), "attempts": len(snapshot.attempts),
            "unknown_attempts": len(unknown), "protected_groups": list(protected_groups),
            "summary_fingerprint": summary_fingerprint, "prepared_status": prepared.status,
            "context_bytes": len(prepared.context), "revision": snapshot.revision}


def create_checkpoint(ConversationMemory, checkpoint):
    memory = ConversationMemory(project_id="memory-example", conversation_id="chat")
    require(memory.prepare(byte_limit=BYTE_LIMIT).status == "empty", "Empty history is explicit")
    for number in range(8):
        closed_exchange(memory, number)
    protected = memory.begin_turn("Await explicit user result; keep this open group. " * 28,
                                  event_id="open-protected")
    history_before = memory.snapshot().history
    prepared = memory.prepare(byte_limit=BYTE_LIMIT)
    require(prepared.status == "needs_compaction", "Long history should request explicit compaction")
    job = memory.begin_compaction(prepared)  # Register BEFORE external caller work.
    result = memory.complete_compaction(job.job_id, caller_summarizer(), expected_revision=job.revision)
    require(result == "applied", "Caller summary was not atomically applied")
    require(memory.snapshot().history == history_before, "Compaction must not delete source history")
    fingerprint = memory.snapshot().summary.fingerprint
    compact = memory.prepare(byte_limit=BYTE_LIMIT)
    require(compact.status == "ready" and len(compact.context) <= BYTE_LIMIT,
            "Accepted summary should yield bounded byte context")
    # New work obtains its own attempt. Simulate an unresolved caller outcome,
    # never an inferred failure or automatic retry of the previous request.
    for number in range(8, 14):
        closed_exchange(memory, number)
    latest = memory.begin_turn("Текущий запрос: исход неизвестен, не повторять автоматически.",
                               event_id="open-latest")
    prepared = memory.prepare(byte_limit=BYTE_LIMIT)
    require(prepared.status == "needs_compaction", "New history should yield a new compaction demand")
    unknown = memory.begin_compaction(prepared)
    memory.mark_unknown(unknown.job_id)
    protected_groups = (protected, latest)
    facts = assert_state(memory, protected_groups=protected_groups, summary_fingerprint=fingerprint)
    exported = memory.export_state()
    require(type(exported) is bytes, "Export must be versioned bytes")
    restored = ConversationMemory.from_state(exported)
    require(restored.export_state() == exported, "Round-trip changed owned history/summary/attempts")
    write_json(checkpoint, {"format": FORMAT, "byte_limit": BYTE_LIMIT,
                           "protected_groups": list(protected_groups),
                           "summary_fingerprint": fingerprint,
                           "memory_state_utf8": exported.decode("utf-8")})
    facts.update({"summary_applied": True, "complete_history_preserved": True,
                  "summary_context_bytes": len(compact.context), "caller_summarizer_calls": 1})
    return facts


def restore_checkpoint(ConversationMemory, checkpoint):
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    require(saved.get("format") == FORMAT and saved.get("byte_limit") == BYTE_LIMIT,
            "Unsupported caller checkpoint format")
    raw = saved["memory_state_utf8"].encode("utf-8")
    memory = ConversationMemory.from_state(raw)
    require(memory.export_state() == raw, "Restored bytes differ from original checkpoint")
    facts = assert_state(memory, protected_groups=tuple(saved["protected_groups"]),
                         summary_fingerprint=saved["summary_fingerprint"])
    facts.update({"fresh_process_restore": True, "caller_summarizer_calls": 0})
    return facts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--smoke", action="store_true")
    mode.add_argument("--restore-smoke", action="store_true")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    args = parser.parse_args()
    if not args.report.is_absolute() or (args.checkpoint is not None and not args.checkpoint.is_absolute()):
        parser.error("--report and --checkpoint must be absolute paths")
    checkpoint = args.checkpoint or args.report.with_suffix(".checkpoint.json")
    if checkpoint.resolve() == args.report.resolve():
        parser.error("Report and checkpoint must be different files")
    result = {"status": "FAIL", "mode": "restore" if args.restore_smoke else "create",
              "scope": "installed standalone Memory, no model/UI/Runner/WASM",
              "byte_limit": BYTE_LIMIT, "token_limit_guarantee": False,
              "model_calls": 0, "checkpoint": str(checkpoint),
              "storage": "caller file IO; no durable exactly-once claim"}
    try:
        sys.meta_path.insert(0, StandaloneImports())
        import agent_native_memory as package
        dist = metadata.distribution("merovinger-memory")
        origin = Path(package.__file__).resolve()
        require(origin == Path(dist.locate_file("agent_native_memory/__init__.py")).resolve(),
                "Expected installed wheel origin")
        require(not dist.requires, "Standalone Memory must not require runtime dependencies")
        result.update({"memory_version": dist.version, "module_origin": str(origin)})
        operation = restore_checkpoint if args.restore_smoke else create_checkpoint
        result.update(operation(package.ConversationMemory, checkpoint))
        result["checkpoint_sha256"] = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
        result["status"] = "PASS"
    except Exception as error:
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    write_json(args.report, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
