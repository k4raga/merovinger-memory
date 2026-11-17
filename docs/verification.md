**English** · [Русский](verification.ru.md)

# Verification

The repository contains independent tests for the public text-history facade and lower-level values, coverage, applicability and counting behavior. They use synthetic data and require no model, UI, database or account.

CI is configured for Python 3.13 on Ubuntu and Windows. A configured job is not a completed platform result: inspect the repository's Actions runs for current outcomes.

Each job builds a wheel, installs that wheel, runs the tests, and executes the checkpoint example in two fresh processes. The example checks complete-history retention, bounded context, explicit summary application, open-group preservation and UNKNOWN restoration. Installed metadata identifies `merovinger-memory`; import paths are checked against the installed distribution.

Run the equivalent checks locally:

```sh
python -m pip install build pytest
python -m build --wheel
python -m pip install --force-reinstall dist/merovinger_memory-0.1.0a1-py3-none-any.whl
python -I -B -m pytest -q tests
```

Then run the two [checkpoint commands](quickstart.md#checkpoint-example). Tests import the installed package, not an injected source path. The lower-level purity check inspects the installed pure modules.

These checks do not establish provider token accuracy, durable exactly-once behavior, production readiness, arbitrary tool-protocol support or cross-service transactions. Local reports/checkpoints are not public fixtures and should remain ignored.

## Recorded local check

On 2026-11-17, a fresh Ubuntu Python 3.13 environment built and installed this repository's wheel: **128 tests passed** (116 lower-level cases, 11 facade cases and one distribution check). Both fresh-process checkpoint commands passed, and both README Python examples ran against the installed package. No model, UI or database was used.

Windows CI is configured but is not claimed as passed by this local run. Check Actions for an actual result after publication. These counts describe this source version, not a performance benchmark or production guarantee.

## Recorded public CI

[Run 44055929711](https://github.com/k4raga/merovinger-memory/actions/runs/44055929711)
at source revision b65b0be succeeded on **Ubuntu and Windows**: wheel build/install,
tests and fresh-process checkpoint/restore. For later changes, inspect the current
[Actions runs](https://github.com/k4raga/merovinger-memory/actions).
