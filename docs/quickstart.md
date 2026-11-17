# Quickstart

Use Python 3.13. Clone `https://github.com/k4raga/merovinger-memory`, create and activate a virtual environment, then run:

```sh
python -m pip install .
```

Import `agent_native_memory`, not `merovinger_memory`. The package name and import name intentionally differ. The [README](../README.md#use-it) is a complete small example.

## Explicit compaction

Using the `memory` instance from the README:

`prepare()` may return `empty`, `ready`, `needs_compaction`, or `cannot_fit`. With `allow_compaction=False`, it can omit optional whole groups to fit; it never silently cuts a mandatory group. `needs_compaction` contains bounded summarizer input, not a ready model conversation.

```python
prepared = memory.prepare(byte_limit=4096)
if prepared.status == "needs_compaction":
    job = memory.begin_compaction(prepared)
    # Your application calls its own summarizer with job.input here.
    # This demonstration supplies text directly; it performs no inference.
    text = "Earlier exchanges discussed a shared plan."
    outcome = memory.complete_compaction(
        job.job_id, text, expected_revision=job.revision
    )
    if outcome == "applied":
        context = memory.prepare(byte_limit=4096, allow_compaction=False)
```

Check the outcome: `applied`, `duplicate`, `collision` and `stale` have different meanings. Do not treat a stale result as published. If an external call's result is uncertain, call `memory.mark_unknown(job.job_id)`; inspect/reconcile it explicitly. Do not label uncertainty a definitive failure merely to retry.

## Checkpoint example

The executable example includes complete history, a protected open group, one accepted summary and an UNKNOWN attempt. The fixed summarizer string is supplied by the caller.

Linux/macOS shell, after installation:

```sh
mkdir -p .runtime/example
python -I -B examples/checkpoint.py --smoke --report "$PWD/.runtime/example/create.json" --checkpoint "$PWD/.runtime/example/chat.json"
python -I -B examples/checkpoint.py --restore-smoke --report "$PWD/.runtime/example/restore.json" --checkpoint "$PWD/.runtime/example/chat.json"
```

Windows PowerShell, after activating the environment:

```powershell
New-Item -ItemType Directory -Force .runtime/example | Out-Null
$directory = (Resolve-Path .runtime/example).Path
python -I -B examples/checkpoint.py --smoke --report "$directory/create.json" --checkpoint "$directory/chat.json"
python -I -B examples/checkpoint.py --restore-smoke --report "$directory/restore.json" --checkpoint "$directory/chat.json"
```

The two commands start separate processes. The second leaves the checkpoint unchanged. The example writes ordinary caller-owned files: it does not implement crash-safe storage or durable transactions. Do not commit checkpoints from real conversations.
