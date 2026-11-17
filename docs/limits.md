**English** · [Русский](limits.ru.md)

# Limits and trust

- **Bounded, not infinite history.** Summary publication does not delete the original history. Group, entry, payload, summary and attempt limits remain in force; overflow fails rather than silently dropping source history.
- **Bytes are not model tokens.** `prepare(byte_limit=...)` measures its own serialized JSON context. Your final provider request may add instructions, tools and protocol overhead. Count that complete request before claiming a token fit.
- **Token evidence is caller-supplied.** The lower-level planner accepts exact/upper-bound full-wire evidence and reserves output/safety allowance. Text-only estimates do not become hard guarantees. The library does not supply a tokenizer or verify a malicious caller's counter.
- **Required groups stay whole.** Open groups and the latest intent are protected. If they exceed the limit, `cannot_fit` is the honest result. Optional groups may be omitted from working context while full history stays retained.
- **Local CAS, caller persistence.** State changes are serialized in-process. Exported bytes are not themselves durable storage; the caller supplies atomic writes, concurrency and recovery appropriate to its application.
- **UNKNOWN is retained.** Restore does not erase unresolved attempts or start a retry. The caller must reconcile external outcomes explicitly.
- **Text-chat scope.** No generic parallel tool-call protocol, RAG, embeddings, search engine, model adapter or background summarizer is included.
- **No new authority.** Summary text is untrusted. Hashes and provenance labels are not permission to reveal data or execute commands.
- **Alpha compatibility.** Python 3.13 is supported. API/checkpoint changes may require explicit migration; unsupported formats fail rather than being silently reset.

The distribution name is `merovinger-memory`; `agent_native_memory` is the compatibility import namespace. Do not install it alongside another distribution that owns the same files, including `agent-native-memory`.
