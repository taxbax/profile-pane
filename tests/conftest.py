"""Test isolation for the pane's panecore.

Two module-level path constants point at the REAL user home
(`~/.hermes/profile-pane/...`). A test that exercises a write path without redirecting
them litters the live system — which is exactly what happened when `snapshot_dir` was
added and the sync tests started retaining trees under the real `~/.hermes`.

Redirected once, here, so a new test cannot reintroduce the leak by forgetting.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dashboard"))

from panecore import apply as A   # noqa: E402
from panecore import trial as T   # noqa: E402

_SANDBOX = Path(tempfile.mkdtemp(prefix="pane-tests-"))
A.SNAPSHOTS = _SANDBOX / "snapshots"
T.TRIALS = _SANDBOX / "trials"
