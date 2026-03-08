# Audit: Default branch naming

## What we check

All active repositories should use `develop` as the default branch.

Exceptions are allowed for:

* Documentation-only repos (may use `main`).
* GitHub Actions repos (conventionally use `main`).
* Archived/deprecated repos (listed in exceptional cases).

## Template

No template -- this is a one-time configuration change.

To change the default branch:

```bash
git branch -m main develop
git push -u origin develop
# In GitHub UI: Settings > General > Default branch > change to develop
git push origin --delete main
```

## Projects

| Project | Current | Status | Issue |
|---------|---------|--------|-------|
| agent-python | develop | compliant | - |
| client-python | develop | compliant | - |
| clingwrap | develop | compliant | - |
| cloudgood | main | non-compliant | |
| imago | main | non-compliant | |
| kerbside | develop | compliant | - |
| kerbside-patches | develop | compliant | - |
| library-utilities | develop | compliant | - |
| occystrap | develop | compliant | - |
| ryll | develop | compliant | - |
| shakenfist | develop | compliant | - |
