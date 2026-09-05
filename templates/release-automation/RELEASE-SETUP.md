# Release Infrastructure Setup

This document describes how to configure PyPI and GitHub to enable automated
releases using GitHub Actions with Sigstore signing.

## Overview

The release process uses:

- **PyPI Trusted Publishers (OIDC)**: No API tokens needed; PyPI trusts the
  GitHub Actions workflow directly
- **Sigstore/gitsign**: Keyless signing for git tags (no GPG private key
  management)
- **GitHub Environments**: Required reviewer approval before releases proceed
- **Protected Tags**: Restrict who can create release tags

## One-Time Setup Steps

### 1. Configure PyPI Trusted Publisher

This allows the GitHub Actions workflow to publish to PyPI without storing any
API tokens.

1. Log in to [pypi.org](https://pypi.org) with your account
2. Navigate to your project: `{{PYPI_PACKAGE_NAME}}`
3. Go to **Settings** (or **Your projects** > **Manage**)
4. Click **Publishing** in the left sidebar
5. Under **Trusted Publishers**, click **Add a new publisher**
6. Fill in the form:
   - **Owner**: `shakenfist`
   - **Repository name**: `{{GITHUB_REPO_NAME}}`
   - **Workflow name**: `release.yml`
   - **Environment name**: `release` (must match the workflow)
7. Click **Add**

The workflow will now be able to publish without any stored credentials.

**Note**: If the `{{PYPI_PACKAGE_NAME}}` package already exists on PyPI under
a different publishing method, you can add the trusted publisher alongside the
existing setup and then remove the old API token once verified.

### 2. Create GitHub Environment with Required Reviewers

This ensures releases only happen after explicit approval.

1. Go to the repository on GitHub: `shakenfist/{{GITHUB_REPO_NAME}}`
2. Click **Settings** > **Environments**
3. Click **New environment**
4. Name it: `release`
5. Click **Configure environment**
6. Under **Environment protection rules**:
   - Check **Required reviewers**
   - Add yourself (and any other trusted maintainers)
   - Optionally add a **Wait timer** (e.g., 5 minutes) for additional safety
7. Under **Deployment branches and tags**:
   - Select **Selected branches and tags**
   - Add a rule: `v*` (to only allow release tags)
8. Click **Save protection rules**

### 3. Configure Protected Tags (Recommended)

This prevents unauthorized users from creating release tags.

1. Go to **Settings** > **Rules** > **Rulesets**
2. Click **New ruleset** > **New tag ruleset**
3. Configure:
   - **Ruleset name**: `Release tags`
   - **Enforcement status**: `Active`
   - **Target tags**: Add pattern `v*`
   - **Rules**: Check **Restrict creations** and **Restrict deletions**
   - **Bypass list**: Add repository admins or specific maintainers,
     **and GitHub Actions** (add the "GitHub Actions" app to the bypass
     list). The release workflow's sign-tag job re-creates and
     force-pushes the release tag as `github-actions[bot]` using
     `GITHUB_TOKEN`; without the Actions bypass that push is rejected
     by this ruleset and every release fails at the signing step.
4. Click **Create**

### 4. Verify Sigstore/Rekor Access

No configuration needed. Sigstore is a public service that:

- Signs artifacts using OIDC identity (the GitHub Actions workflow identity)
- Records signatures in a public transparency log (Rekor)
- Requires no key management

Verification can be done by anyone using `cosign` or `gitsign verify`.

## How Releases Work

1. A maintainer pushes a tag matching `v*` (e.g., `v0.1.0`)
2. The `release.yml` workflow triggers
3. The workflow builds the package and waits for environment approval
4. A required reviewer approves the release in GitHub's UI
5. The workflow:
   - Creates a signed git tag using gitsign (Sigstore)
   - Generates Sigstore attestations for the built artifacts
   - Publishes to PyPI using OIDC (no tokens)
   - Creates a GitHub Release with the artifacts

### Running the workflow by hand

The workflow also offers `workflow_dispatch`. A manual run builds the
package and runs `twine check`, and stops there: it never signs a tag,
never uploads to PyPI, and never creates a release. Use it to confirm
the package still builds without cutting a release.

That is enforced by a guard on each publishing job:

```yaml
if: github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')
```

Both clauses earn their place. Without the ref test, a dispatch aimed at
a branch would have `sign-tag` treat `refs/heads/<branch>` as a tag name
and force-push a `refs/tags/refs/heads/<branch>` ref. Without the event
test, a dispatch aimed at an *existing tag* would re-sign and force-push
it, rewriting a signed object someone may already have verified.

Nothing is lost by requiring `push`. Re-running a failed release goes
through GitHub's "Re-run jobs", which replays the original push event
and so satisfies the guard.

### Where the distribution lives

The two publishing jobs read the built distribution from different
places, which is deliberate.

`publish-pypi` checks out and works in `dist/` inside the workspace.
`pypa/gh-action-pypi-publish` is a composite action that delegates to a
Docker container action, and a container is given the workspace at
`/github/workspace` and nothing else of the runner's filesystem. So
`packages-dir` has to be relative to the workspace; an absolute path
fails inside the container however it is written, including one under
`RUNNER_TEMP`, which is not mounted at all. The action also writes its
container trampoline into `.github/.tmp/` in the workspace, so it needs
a real checkout regardless.

`github-release` does not check out and downloads into
`${{ runner.temp }}`. `softprops/action-gh-release` is a JavaScript
action running on the host, so an absolute path is fine there.

Either way the point is the same: these runners are persistent, and
`download-artifact` extracts *into* its target rather than replacing
it, so a job must not read a directory some earlier job may have left
files in. Checkout gives that guarantee by cleaning
(`git clean -ffdx`); `runner.temp` gives it by being per job.

## Verifying Releases

### Verify Git Tag Signature

```bash
# Install gitsign
go install github.com/sigstore/gitsign@latest

# Verify a tag
gitsign verify --certificate-identity-regexp='.*' \
    --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
    v0.1.0
```

### Verify PyPI Package Attestation

```bash
# PyPI shows attestation status on the package page
# Look for the "Provenance" section
```

### Verify with Cosign

```bash
# Install cosign
go install github.com/sigstore/cosign/v2/cmd/cosign@latest

# Verify artifact attestation
cosign verify-attestation \
    --certificate-identity-regexp='.*' \
    --certificate-oidc-issuer='https://token.actions.githubusercontent.com' \
    {{PYPI_PACKAGE_NAME}}-0.1.0.tar.gz
```

## Troubleshooting

### "Environment not found" Error

Ensure the environment name in the workflow (`release`) exactly matches the
environment created in GitHub Settings.

### "Publisher not found" Error on PyPI

- Verify the workflow filename matches exactly (case-sensitive)
- Verify the environment name matches exactly
- Ensure you're using the correct PyPI account (not TestPyPI)

### Tag Signature Verification Fails

- Ensure you're checking against the correct OIDC issuer
- The certificate identity will be the workflow's identity, not a personal
  email

### Approval Not Requested

- Ensure the tag matches the deployment branch/tag rules (e.g., `v*`)
- Check that required reviewers are configured on the environment

## Security Considerations

- **No long-lived secrets**: Neither GPG keys nor PyPI tokens are stored
- **Audit trail**: All releases are logged in GitHub Actions and Sigstore's
  Rekor transparency log
- **Multi-party approval**: Required reviewers prevent unilateral releases
- **Immutable provenance**: Sigstore attestations cryptographically link
  artifacts to the exact source commit
