# Release Strategy & Process

This document outlines the release philosophy, distribution channels, and release process for the `asana-org` project.

## Repository

**GitHub**: https://github.com/zb-ss/asana-org

## Release Philosophy

### Should we release?
**Yes.** This tool provides a unique and powerful workflow for Emacs users who need to integrate with Asana in a professional environment. It fills a gap for robust, bidirectional synchronization that respects both Org-mode's flexibility and Asana's structured data.

### Versioning
We follow [Semantic Versioning](https://semver.org/). Breaking changes to the CLI contract or the Elisp interface will trigger a major version bump.

---

## Distribution Channels

### 1. GitHub Releases (Primary)
- Source for all tagged versions and changelogs
- Trigger point for automated PyPI publishing

### 2. PyPI (Automated)
The Python bridge CLI is published automatically when a GitHub Release is created.

**Package**: `asana-org-bridge`
**Install**: `pipx install asana-org-bridge`

**One-Time Setup (PyPI Trusted Publisher)**:
1. Go to https://pypi.org/manage/project/asana-org-bridge/settings/publishing/
2. Add a new Trusted Publisher:
   - **PyPI Project Name**: `asana-org-bridge`
   - **Owner**: `zb-ss`
   - **Repository**: `asana-org`
   - **Workflow name**: `pypi-publish.yml`
   - **Environment name**: `pypi`
3. Save the configuration
4. Future releases will publish automatically via OIDC (no API tokens needed)

### 3. MELPA (Manual Initial Submission)
The Emacs package requires an initial manual submission to MELPA. After that, MELPA updates automatically from the source repository.

See the [MELPA Submission Guide](melpa-submission.md) for detailed instructions.

**Important**: MELPA cannot be auto-published directly from this repository. The process is:

1. **First-time setup (manual)**:
   - Fork https://github.com/melpa/melpa
   - Create recipe file: `recipes/asana-org` with content:
     ```
     (asana-org :fetcher github :repo "zb-ss/asana-org" :files ("elisp/*.el"))
     ```
   - Submit PR to melpa/melpa
   - After merge, MELPA will automatically build and publish updates

2. **Ongoing maintenance (automatic)**:
   - MELPA monitors the upstream repository
   - New versions are detected from Version header in `elisp/asana-org.el`
   - No further action required after initial submission

---

## Release Process

For a detailed step-by-step guide, see the [Release Checklist Template](../.github/ISSUE_TEMPLATE/release-checklist.md).

### Creating a New Release

1. **Update version numbers**:
   - `bridge/pyproject.toml` - Python bridge version
   - `elisp/asana-org.el` - Update `;; Version:` header

2. **Update CHANGELOG** (if applicable)**

3. **Commit and tag**:
   ```bash
   git commit -am "chore: bump version to X.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

4. **Create GitHub Release**:
   - Go to https://github.com/zb-ss/asana-org/releases/new
   - Select the tag
   - Write release notes
   - Publish

5. **Automatic publishing**:
   - PyPI: Workflow `.github/workflows/pypi-publish.yml` triggers automatically
   - MELPA: Updates on next build cycle (after initial manual submission)

---

## Maintainer Checklist

### Pre-Release
- [ ] All tests passing (`pytest` in `bridge/`, byte-compile checks)
- [ ] Version bumped in `bridge/pyproject.toml`
- [ ] Version bumped in `elisp/asana-org.el`
- [ ] CHANGELOG updated (notable changes)
- [ ] Documentation reviewed for accuracy

### Release Day
- [ ] Tag commit with `vX.Y.Z`
- [ ] Push tag to origin
- [ ] Create GitHub Release with notes
- [ ] Verify PyPI workflow succeeded (check Actions tab)
- [ ] Verify PyPI package appears on https://pypi.org/project/asana-org-bridge/

### Post-Release
- [ ] Verify MELPA picked up changes (after initial submission)
- [ ] Update any roadmap/docs if needed

---

## CI/CD Workflows

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `pypi-publish.yml` | GitHub Release | Build & publish Python bridge to PyPI |
| `melpa-validation.yml` | Tag push, Release, PR to elisp/ | Validate Emacs package for MELPA compliance |

---

## Status

- ✅ PyPI: Automated publishing configured
- ⏳ MELPA: Awaiting initial manual recipe submission to melpa/melpa

---

## Roadmap

- [x] **PyPI Publication**: Automated via GitHub Actions (Trusted Publishing)
- [ ] **MELPA Submission**: Submit recipe to melpa/melpa
- [ ] **Documentation**: Add comprehensive docstrings to all Elisp functions
- [ ] **User Feedback**: Conduct beta testing to refine UX
- [ ] **Feature**: Attachment support (read-only)
- [ ] **Feature**: Tag syncing (Asana tags ↔ Org-mode tags)