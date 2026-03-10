# Installation Guide

Detailed instructions for installing the Asana ↔ Org-mode integration components.

## Prerequisites

-   **Python 3.11+** - check with `python3 --version`
-   **Emacs 28.1+** with `transient` 0.4.0+
-   **Asana Personal Access Token** - generate at [Asana Developer Console](https://app.asana.com/0/developer-console)

## 1. Bridge CLI (Python)

### Using a virtual environment (recommended)

```bash
git clone https://github.com/zb-ss/asana-org.git
cd asana-org/bridge

python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

After installation, make the binary accessible system-wide:

```bash
# Option A: Symlink to ~/.local/bin (must be in PATH)
ln -sf "$(pwd)/.venv/bin/asana-org-bridge" ~/.local/bin/asana-org-bridge

# Option B: Add the venv bin to PATH in your shell profile
echo 'export PATH="/path/to/asana-org/bridge/.venv/bin:$PATH"' >> ~/.bashrc
```

### Using uv (fast alternative)

[uv](https://github.com/astral-sh/uv) is a fast Python package installer.

```bash
cd asana-org/bridge
uv venv
uv pip install -e .
```

### Using pipx (global install, no venv management)

```bash
pipx install asana-org-bridge
```

### Verify installation

```bash
asana-org-bridge --help
asana-org-bridge doctor
```

## 2. Configure Authentication

Add your Asana PAT to your shell profile so it persists:

```bash
# For bash
echo 'export ASANA_PAT="your_token_here"' >> ~/.bashrc
source ~/.bashrc

# For zsh
echo 'export ASANA_PAT="your_token_here"' >> ~/.zshrc
source ~/.zshrc
```

Then initialize the database:

```bash
asana-org-bridge db-init
```

## 3. Emacs Client

### Doom Emacs

Add to `~/.config/doom/packages.el`:

```elisp
(package! asana-org
  :recipe (:host github :repo "zb-ss/asana-org" :files ("elisp/*.el")))
```

Add to `~/.config/doom/config.el`:

```elisp
(use-package! asana-org
  :defer t
  :commands (asana-org-transient asana-org-sync-pull asana-org-sync-preview
             asana-org-sync-apply asana-org-move-task asana-org-comment-append)
  :init
  (setq asana-org-bridge-binary "asana-org-bridge"
        asana-org-root-directory (expand-file-name "~/org/asana")
        asana-org-dry-run t)  ; Set to nil once you're comfortable
  :config
  (asana-org-transient-setup-keybindings))
```

Then run:
```bash
~/.config/emacs/bin/doom sync
```

Restart Emacs for changes to take effect.

### For a local checkout (development)

If you cloned the repo locally instead of installing from GitHub:

```elisp
;; in packages.el
(package! asana-org :recipe (:local-repo "~/projects/asana-org/elisp"
                             :files ("*.el")))
```

### Vanilla Emacs

```elisp
(add-to-list 'load-path "/path/to/asana-org/elisp")
(require 'asana-org)
(setq asana-org-bridge-binary "asana-org-bridge"
      asana-org-root-directory (expand-file-name "~/org/asana"))
(asana-org-transient-setup-keybindings)
```

### Spacemacs

Add to `dotspacemacs-additional-packages`:

```elisp
(asana-org :location (recipe :fetcher github
                             :repo "zb-ss/asana-org"
                             :files ("elisp/*.el")))
```

Then configure in `dotspacemacs/user-config`:

```elisp
(require 'asana-org)
(setq asana-org-bridge-binary "asana-org-bridge"
      asana-org-root-directory (expand-file-name "~/org/asana"))
(asana-org-transient-setup-keybindings)
```

## 4. Post-Installation

1. Run `asana-org-bridge doctor` to verify everything is connected.
2. Optionally configure [project mappings](configuration.md#project-mapping-example).
3. Run `M-x asana-org-sync-pull` for your first sync. The `~/org/asana/` directory is created automatically.
