"""Main entry point for Asana Org Bridge CLI."""

from asana_org_bridge.commands import app

# Make the app available when imported
__all__ = ["app"]


if __name__ == "__main__":
    app()
