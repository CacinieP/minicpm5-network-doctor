# CI and release checks

The `CI` workflow runs for pushes to `main`, pull requests targeting `main`, version tags
(`v*`), and manual dispatch. It needs no model weights, model service, GPU, or API key.
Dependency installation and vulnerability lookup use the network; the tests and installed-package
smoke commands block network access.

## Required checks

| Check | Coverage |
| --- | --- |
| Lint and metadata | Ruff lint/format, version agreement, packaged prompt, YAML skill metadata, changelog entry, and tag/version agreement on tag runs |
| Unit and regression tests | Python 3.10, 3.11, 3.12, 3.13, and 3.14 on Ubuntu; Python 3.12 on macOS and Windows |
| Coverage | Combined statement/branch coverage, minimum 85% on every test runner |
| Build | Build the sdist, build the wheel through that sdist, then run strict Twine metadata validation |
| Installed distributions | Install wheel and sdist independently in fresh environments outside the checkout on Ubuntu 3.10/3.14 and macOS/Windows 3.12 |
| Dependency audit | Check installed dependencies against known advisories; exclude the unpublished editable project itself |
| CI passed | Fail unless every required job succeeds, including all matrix entries |

Each installed-distribution smoke checks the console command and `python -m` entrypoint,
version metadata, `--help`, `--list-tools --json`, the seven tool schemas, and the packaged system
prompt. It does not run a diagnosis or contact a model backend. Temporary environments are
retained for debugging.

Tests use `pytest-socket` and a DNS guard; proxy and model environment settings are isolated.
Unexpected socket or DNS operations fail instead of contacting a developer's local service.
A passing CI run demonstrates tested program and packaging behavior. It does **not** establish
real-model tool selection, convergence, or diagnostic accuracy; those still require the
[real-model benchmark](benchmark-plan.md).

## Evidence and release artifacts

- Each test job retains JUnit and coverage XML for 14 days.
- The `distributions` artifact retains the wheel, sdist, and `SHA256SUMS` for 30 days.
- Official actions are pinned to commit SHAs; Dependabot proposes monthly action updates.
- Workflow permissions are read-only. No job publishes a release or uploads to PyPI.

Publish only artifacts from the successful CI run for the intended release commit. Confirm the
tag points to that commit and the tag run succeeds before making the GitHub Release public.
After publishing, independently download all assets, verify their checksums, and run the same
installed-package smoke checks on the public files.

## Run checks locally without Ollama

```bash
python -m pip install -e ".[dev]" build twine
ruff check .
ruff format --check .
python -m pytest --cov=minicpm_network_doctor
python scripts/verify_metadata.py
python -m build
python -m twine check --strict dist/*
python scripts/verify_package.py dist
```

`verify_package.py` accepts `--work-dir` to choose a parent directory outside the checkout for
its retained temporary environments. Start from an empty `dist` directory so stale versions
cannot be mistaken for the release being checked. `verify_metadata.py` expects an editable
installation of the current checkout, as used by CI.
