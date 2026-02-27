# Development Guide

Instructions for setting up a development environment and contributing to `asana-org`.

## Bridge Development (Python)

The bridge is built with Python 3.11+ using `typer` for the CLI and `httpx` for API interactions.

### Setup

We recommend using `uv` for fast dependency management.

```bash
cd bridge
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### Testing

We use `pytest` for unit and integration tests.

```bash
pytest
```

### Linting & Typing

We use `ruff` for linting and `mypy` for static type checking.

```bash
ruff check src/
mypy src/
```

## Emacs Client Development (Elisp)

### Setup

Add the `elisp` directory to your `load-path`.

```elisp
(add-to-list 'load-path "/path/to/asana-org/elisp")
(require 'asana-org)
```

### Testing

(Add instructions for Elisp testing once a framework is established, e.g., `ert` or `buttercup`).

## CLI Contract

When making changes that affect the communication between the Emacs client and the Python bridge, please refer to the [CLI Contract](cli-contract.md). Any breaking changes to this contract should be accompanied by a version bump.
