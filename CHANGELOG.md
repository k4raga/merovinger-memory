**English** · [Русский](CHANGELOG.ru.md)

# Changelog

## Unreleased · documentation

- English/Russian covers, mechanism diagrams and social-preview assets.
- Language navigation at the top of README and all four technical guides.
- Complete Russian quickstart, architecture, limits, verification and contributor/release notes.
- Library source, runtime behavior, examples and test assertions are unchanged.

## 0.1.0a1 — Alpha

- Bounded text conversation history with explicit group closure.
- Whole-group byte-context selection and protected open/latest groups.
- Caller-owned compaction with revision checks and attempt outcomes.
- Versioned state export/restore preserving accepted summaries and UNKNOWN.
- Lower-level immutable memory values, coverage and token-evidence planner.
- Standalone `merovinger-memory` distribution with no runtime dependencies; compatibility namespace `agent_native_memory`.

No PyPI publication, production-readiness or durable exactly-once guarantee is implied.
