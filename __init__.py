"""profile-pane — native desktop pane for cross-profile configuration.

The Python half is the plugin router (`dashboard/plugin_api.py`); the desktop half
is the pane (`desktop/plugin.js`). This module exists because the plugin loader
requires a package `__init__.py` at the plugin root — without it the whole plugin
is rejected and neither half loads.
"""


def register(ctx):
    """No-op registration — the plugin is a desktop pane, not an agent-extension plugin.

    The catalog's capability probe imports this module and calls ``register(ctx)`` to
    record tools/hooks/middleware. profile-pane registers none of those: its surface is
    the desktop pane plus the FastAPI router in ``dashboard/plugin_api.py``.
    """