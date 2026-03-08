# Audit: GitHub security settings and CodeQL

## What we check

### Repository security settings

All active repositories should have these settings enabled in
Settings > Code security and analysis:

| Setting | Recommended |
|---------|-------------|
| Dependabot security updates | Enabled |
| Secret scanning | Enabled |
| Secret scanning push protection | Enabled |

Additionally recommended:

| Setting | Recommended |
|---------|-------------|
| Delete branch on merge | Enabled |
| Allow auto-merge | Enabled |

### GitHub CodeQL

All **public** projects should have a
`.github/workflows/codeql-analysis.yml` for advanced security
scanning.

**Private repos are excluded:** CodeQL requires a paid GHAS license
for private repos. Without GHAS, the workflow will fail.

The CodeQL workflow must have job-level permissions:

```yaml
jobs:
  analyze:
    permissions:
      actions: read
      contents: read
      security-events: write
```

The `actions: read` permission is required for workflow run
telemetry.

## Template

CodeQL template: `templates/codeql/`
See: `templates/codeql/README.md`

Security settings: UI-only configuration, no template needed.

## Projects

| Project | Dependabot | Secret scan | CodeQL | Issue |
|---------|------------|-------------|--------|-------|
| agent-python | non-compliant | non-compliant | compliant | |
| client-python | compliant | non-compliant | non-compliant | |
| clingwrap | compliant | compliant | compliant | - |
| cloudgood | N/A | N/A | N/A | - |
| imago | N/A | N/A | N/A | - |
| kerbside | non-compliant | non-compliant | compliant | |
| kerbside-patches | non-compliant | non-compliant | non-compliant | |
| library-utilities | non-compliant | non-compliant | non-compliant | |
| occystrap | compliant | compliant | compliant | - |
| ryll | non-compliant | non-compliant | non-compliant | |
| shakenfist | compliant | non-compliant | compliant | |

N/A for imago: private repo, requires GHAS license.
N/A for cloudgood: no source code to scan.
