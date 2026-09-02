# PyPI publication plan (deferred)

Status as of 2026-09-02: **deferred**.

Releases are distributed through GitHub Releases only. The repository has no
active PyPI publishing workflow, no GitHub `pypi` environment, and no configured
PyPI Trusted Publisher. Pushing a version tag must not publish anything to PyPI.

## Why publication is deferred

- The project is still marked Alpha and GitHub Releases cover the current trial
  and source-install use cases.
- The package name and independent-community-project wording should remain stable
  before creating a permanent package identity on PyPI.
- A PyPI release cannot be overwritten with new files under the same version.
- There is not yet enough user demand for a registry install path to justify a
  second public distribution channel.

## Activation criteria

Reconsider PyPI publication when all of the following are true:

1. Users need `pipx install minicpm-network-doctor` or an equivalent registry install.
2. The CLI and package name are stable enough for a Beta release.
3. CI and isolated wheel-install checks pass across supported Python versions.
4. The maintenance and security-response expectations of a public package are accepted.
5. The exact first version, package name, publisher identity, and public consequences
   receive an explicit release-time approval.

## Future implementation

If the criteria are met, prepare PyPI as a separate, reviewed change:

1. Add a dedicated tag-triggered workflow using PyPI Trusted Publishing (OIDC).
2. Create a protected GitHub Environment named `pypi` with required reviewers.
3. Register the exact repository, workflow filename, and environment as a pending
   Trusted Publisher on PyPI.
4. Build and validate the wheel and source distribution in an isolated environment.
5. Obtain a separate confirmation before pushing the first publishing tag.
6. Verify the public PyPI project, exact version, hashes, and clean installation.

Until that plan is explicitly activated, GitHub Releases remain the only public
release channel.
