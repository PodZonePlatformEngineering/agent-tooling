# Tests

Primitive tests live here. Each primitive has a corresponding test script under
`tests/<category>/test-<primitive-name>.sh`.

Tests are integration tests — they exercise the real fixture environments documented
in `tests/fixtures/README.md`. There are no mocks.

## Running tests

```bash
# All tests
./tests/run-all.sh

# Single primitive
./tests/qdrant/test-add-qdrant-point.sh
```

Tests are gated by `install.sh` — primitives must pass tests before being deployed
to the workstation.

## Note for external readers

Some tests reference internal PodZone task IDs (`PROJ-XXX`, `T-YYY`, `CC-NNN`) in
their docstrings as origin pointers, and some fixture data uses the maintainer's
workstation path (`/Users/martincolley/...`) as parser input. These are inert
strings — never executed against a real path, never written. The live integration
tests in `tests/test_primitives.sh` default to the PodZone fixture environment
documented in `tests/fixtures/README.md`; to point them at your own Qdrant +
Ollama, set `AGENTSONLY_QDRANT_URL`, `PODZONE_QDRANT_APIKEY`, and `OLLAMA_HOST`.
