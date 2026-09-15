"""profile-pane — native desktop pane for cross-profile configuration.

The Python half is the plugin router (`dashboard/plugin_api.py`); the desktop half
is the pane (`desktop/plugin.js`). This module exists because the plugin loader
requires a package `__init__.py` at the plugin root — without it the whole plugin
is rejected and neither half loads.
"""
