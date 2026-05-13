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
to the workstation. This is T-011 work; stubs currently exit 0.
