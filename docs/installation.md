# Installation Guide

Detailed instructions for installing the Asana ↔ Org-mode integration components.

## 1. Bridge CLI (Python)

The bridge requires Python 3.11+.

### Using uv (recommended)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer and resolver.

```bash
# Clone the repository
git clone https://github.com/zb-ss/asana-org.git
cd asana-org/bridge

# Install in editable mode
uv pip install -e .
```

### Using pip

```bash
# Clone the repository
git clone https://github.com/zb-ss/asana-org.git
cd asana-org/bridge

# Install in editable mode
pip install -e .
```

## 2. Emacs Client

### Requirements

-   Emacs 28.1+
-   `transient` 0.4.0+

### Manual Installation

Add the `elisp` directory to your `load-path` and require the package.

```elisp
(add-to-list 'load-path "/path/to/asana-org/elisp")
(require 'asana-org)

;; Recommended: Setup keybindings
(asana-org-transient-setup-keybindings)
```

### Using Doom Emacs

Add the following to your configuration:

```elisp
;; in packages.el
(package! asana-org
  :recipe (:host github :repo "zb-ss/asana-org" :files ("elisp/*.el")))

;; in config.el
(use-package! asana-org
  :after org
  :config
  (asana-org-transient-setup-keybindings))
```

## 3. Post-Installation Setup

After installing both components, follow the [Quickstart](../README.md#quickstart) in the main README to configure your credentials and perform your first sync.
