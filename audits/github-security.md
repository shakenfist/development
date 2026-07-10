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

<!-- consistency-audit:begin -->
*This table is regenerated daily by the consistency audit
workflow from `scripts/audit-check.py` results; do not edit
it by hand.*

Last regenerated: 2026-07-10T09:39:52.855355+00:00

| Project | Status | Issue |
|---------|--------|--------|
| agent-python | non-compliant | shakenfist/agent-python#81 |
| client-python | compliant | - |
| clingwrap | compliant | - |
| cloudgood | non-compliant | shakenfist/cloudgood#5 |
| divergulent | non-compliant | shakenfist/divergulent#41 |
| imago | non-compliant | - |
| kerbside | non-compliant | shakenfist/kerbside#58 |
| kerbside-patches | non-compliant | shakenfist/kerbside-patches#952 |
| library-utilities | non-compliant | shakenfist/library-utilities#36 |
| occystrap | compliant | - |
| ryll | compliant | - |
| shakenfist | non-compliant | shakenfist/shakenfist#3056 |

Details for non-compliant projects:

- **agent-python** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **cloudgood** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **divergulent** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled
- **imago** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
- **kerbside** (Status): Missing .github/workflows/codeql-analysis.yml
- **kerbside-patches** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled
- **library-utilities** (Status): Missing .github/workflows/codeql-analysis.yml; Secret scanning not enabled; Secret scanning push protection not enabled
- **shakenfist** (Status): Secret scanning not enabled; Secret scanning push protection not enabled
<!-- consistency-audit:end -->
