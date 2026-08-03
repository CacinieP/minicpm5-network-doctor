# Publishing to PyPI

This project publishes to PyPI via **Trusted Publishing (OIDC)** — no API tokens
stored as GitHub secrets. Publication is triggered automatically by pushing a
`v*` tag.

## One-time setup (do this before the first release)

Trusted Publishing links a PyPI project to a GitHub workflow. You only configure
this once:

1. **Reserve the project on PyPI** (only needed if the name is not yet registered).
   For the very first release you can use a short-lived API token, or — preferred —
   let the first trusted-publishing run create it (PyPI creates the project on the
   first successful OIDC publish if the publisher is pre-registered).

2. **Configure the Trusted Publisher on PyPI:**
   - Go to <https://pypi.org/manage/account/publishing/>
   - Add a new publisher of type **GitHub**
   - Fill in:
     - PyPI project name: `minicpm-network-doctor`
     - Owner: `CacinieP`
     - Repository: `minicpm5-network-doctor`
     - Workflow filename: `publish.yml`
     - Environment name: `pypi`

3. **Create a GitHub Environment named `pypi`:**
   - Go to the repo **Settings → Environments → New environment**
   - Name it `pypi`
   - (Optional but recommended) add required reviewers so a tag push alone can't
     publish without approval.

After this, `git push` a `v*` tag and the workflow publishes on PyPI automatically.

## Standard release procedure

```bash
# 1. Make sure main is green and up to date
git checkout main
git pull
pytest && ruff check . && ruff format --check .

# 2. Bump the version in two places
#    pyproject.toml        -> version = "0.x.0"
#    src/minicpm_network_doctor/__init__.py -> __version__ = "0.x.0"

# 3. Commit the bump
git add pyproject.toml src/minicpm_network_doctor/__init__.py
git commit -m "chore: bump version to 0.x.0"
git push

# 4. Tag and push — this triggers publish.yml
git tag -a v0.x.0 -m "v0.x.0: <short description>"
git push origin v0.x.0
```

Watch the workflow:
<https://github.com/CacinieP/minicpm5-network-doctor/actions/workflows/publish.yml>

The `build` job builds the wheel + sdist, runs `twine check`, and verifies the
wheel actually imports and loads its prompt from a fresh venv. Only if that
passes does the `publish` job upload to PyPI.

## Verify after publishing

```bash
pip install minicpm-network-doctor==0.x.0
minicpm-network-doctor --version
```

The package on PyPI:
<https://pypi.org/project/minicpm-network-doctor/>

## Rollback

PyPI does not allow re-uploading the same version or deleting files (except within
the first hour). If a broken release ships:

1. Yank it: `pip download` still works but it is excluded from `*` resolvers:
   `https://pypi.org/manage/project/minicpm-network-doctor/releases/`
2. Bump to `0.x.1` and publish a fixed release.
3. Leave the yanked version in place — never delete release history.

## Why no API token?

Trusted Publishing (PEP 541 / OIDC) issues a short-lived token per workflow run,
scoped to this exact repository + workflow + environment. There is no long-lived
secret to leak or rotate. This is the PyPI-recommended mechanism since 2024.
