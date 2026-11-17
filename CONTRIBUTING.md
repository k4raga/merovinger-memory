# Contributing

Merovinger Memory is alpha. Keep changes small and include a synthetic reproducer.

1. Use Python 3.13 and a virtual environment.
2. Install development tools with `python -m pip install build pytest`.
3. Build and install the wheel, then run `python -I -B -m pytest -q tests`.
4. Run the create/restore [example](docs/quickstart.md#checkpoint-example) when changing history, compaction or checkpoints.

Keep the English and Russian READMEs consistent. Preserve `agent_native_memory` import compatibility. Document deliberate API/format changes and update the changelog.

Do not introduce mandatory model/UI dependencies, hidden summarizer calls or automatic UNKNOWN retries. Do not commit real conversations, credentials, local environments or runtime output. Test with synthetic inputs. Explain what a verification result proves and what it does not.

Code and original documentation/graphics are provided under [MIT](LICENSE).
