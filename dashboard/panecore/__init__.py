"""panecore — pure, testable logic for the profile pane.

Kept separate from plugin_api.py so every rule below is unit-testable without a
FastAPI app or a live gateway. plugin_api.py is a thin router over these.
"""

from __future__ import annotations
