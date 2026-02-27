---
name: Release Checklist
about: Track the progress of a new release
title: 'Release vX.Y.Z'
labels: release
assignees: ''
---

## Pre-release Checks
- [ ] Bump version in `bridge/pyproject.toml`
- [ ] Bump version in `elisp/asana-org.el` (Version header)
- [ ] Update `CHANGELOG.md` with new changes
- [ ] Run tests: `pytest` in `bridge/`
- [ ] Run linting: `ruff check src tests` and `ruff format --check src tests` in `bridge/`
- [ ] Run type checking: `mypy src tests` in `bridge/`
- [ ] Byte-compile Elisp: `emacs -Q -batch -f batch-byte-compile elisp/*.el`
- [ ] Run `package-lint` on Elisp files
- [ ] Ensure documentation is up to date

## Tag & GitHub Release
- [ ] Commit version bumps: `git commit -am "chore: bump version to vX.Y.Z"`
- [ ] Create git tag: `git tag -a vX.Y.Z -m "Release vX.Y.Z"`
- [ ] Push changes and tag: `git push origin main --tags`
- [ ] Create GitHub Release at https://github.com/zb-ss/asana-org/releases/new
  - [ ] Select tag `vX.Y.Z`
  - [ ] Copy-paste CHANGELOG entries into release notes

## Post-release Verification
- [ ] Verify PyPI publish success: Check [GitHub Actions](https://github.com/zb-ss/asana-org/actions)
- [ ] Verify package on PyPI: https://pypi.org/project/asana-org-bridge/
- [ ] Verify MELPA build (after initial submission): https://melpa.org/#/asana-org
- [ ] Close this issue

## Rollback Notes
If PyPI publish fails:
1. Delete the tag locally: `git tag -d vX.Y.Z`
2. Delete the tag on remote: `git push --delete origin vX.Y.Z`
3. Fix the issue and restart the process.
