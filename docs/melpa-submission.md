# MELPA Submission Guide

This document provides the necessary information for submitting `asana-org` to [MELPA](https://melpa.org/).

## MELPA Recipe

Add the following snippet to a new file `recipes/asana-org` in your fork of `melpa/melpa`:

```elisp
(asana-org :fetcher github :repo "zb-ss/asana-org" :files ("elisp/*.el"))
```

## Initial Submission Process

1. **Fork MELPA**: Fork the [melpa/melpa](https://github.com/melpa/melpa) repository.
2. **Clone your fork**: `git clone https://github.com/<your-username>/melpa.git`
3. **Create a branch**: `git checkout -b add-asana-org`
4. **Add the recipe**: Create `recipes/asana-org` with the snippet above.
5. **Validate the recipe**:
   - Run `make recipes/asana-org` to ensure it builds locally.
   - Use `package-lint` on `elisp/asana-org.el` in this repo.
6. **Commit and Push**: `git add recipes/asana-org && git commit -m "Add asana-org" && git push origin add-asana-org`
7. **Open a Pull Request**: Submit the PR to `melpa/melpa`.

## PR Template

### Title
`Add asana-org`

### Body
```markdown
Add `asana-org`, a package for bidirectional synchronization between Emacs Org-mode and Asana.

- **Repo**: https://github.com/zb-ss/asana-org
- **Description**: Sync Org-mode files with Asana tasks using a Python bridge.
- **License**: GPL-3.0-or-later
```

## Common Rejection Reasons & Checklist

Before submitting, ensure the following are correct in `elisp/asana-org.el`:

- [ ] **Headers**: Must have standard Elisp headers (`;;; asana-org.el --- ...`).
- [ ] **Version**: Must have a `;; Version: X.Y.Z` header.
- [ ] **Package-Requires**: Must list all dependencies (e.g., `(emacs "28.1")`, `transient`).
- [ ] **Loadability**: The package must be loadable without errors. Run `emacs -Q -batch -l elisp/asana-org.el`.
- [ ] **Checkdoc**: Run `M-x checkdoc` and fix all style warnings.
- [ ] **Package-lint**: Run `M-x package-lint-current-buffer` (requires `package-lint` package).
- [ ] **Namespace**: All functions and variables must be prefixed with `asana-org-`.
- [ ] **No Trailing Whitespace**: Ensure no trailing whitespace in the file.
