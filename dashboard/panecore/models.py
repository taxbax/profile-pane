"""The provider/model SELECTION layer — which model each member runs on.

This is `config.yaml`'s `model` block plus `fallback_providers`: the provider and the model
name. It is a THIRD kind of thing, distinct from the two layers either side of it:

  * SOUL / MEMORY / USER are documents — prose, block files.
  * SECRETS are values — credentials, and the pane never renders one.
  * MODELS are the pointer to the account and the model. There is no secret here (the KEY
    lives in `.env`, which is the secrets layer), but there IS consequence: pushing this
    changes what every member RUNS ON, and a member whose provider has no key for the pushed
    model will fail at its next call. So it is offered, named, and reversible — never silent.

Why not just copy `config.yaml`?
  * It holds far more than the model: `agent.max_turns`, personalities, aliases, tool
    settings. Pushing the whole file would overwrite a member's unrelated tuning.
  * It is not a plain file we can write blindly: it has no CAS, and the running gateway
    reads it. We write atomically through a snapshot, and only the keys we own.

`base_url` deliberately does NOT move. It is an endpoint, and an anchor pointing at a local
server would send every member at a host that may not exist for them. It stays each
member's own.
"""

from __future__ import annotations

import copy
import os
import tempfile
from pathlib import Path

import yaml

from . import apply as apply_mod

# The keys this layer owns, in one place so read and write cannot drift apart. A key that
# one function moves and the other does not know about is how a layer silently half-works.
OWNED = ("default", "provider")          # inside the `model:` mapping
TOP_LEVEL_OWNED = ("fallback_providers",)


def config_path(profile_dir: Path) -> Path:
    return Path(profile_dir) / "config.yaml"


def _load(profile_dir: Path) -> dict:
    p = config_path(profile_dir)
    if not p.exists():
        return {}
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        # An unreadable config is a FAILURE, not an empty one. Returning {} would let a push
        # rewrite the file from nothing and destroy every unrelated setting in it.
        raise


def read_selection(profile_dir: Path) -> dict:
    """What this profile runs on, as a flat item list the panel can render.

    One shape, same as every other layer's items: `[{key, label, preview, chars, meta}]`.
    `key` is the dotted path (`model.provider`, `model.default`, `fallback_providers`) so a
    pick names a specific setting and not a position.
    """
    try:
        d = _load(profile_dir)
    except Exception as e:
        return {"ok": False, "error": f"config.yaml unreadable: {type(e).__name__}", "items": []}

    m = d.get("model") or {}
    if not isinstance(m, dict):
        m = {"default": str(m)}

    items: list[dict] = []
    for k in OWNED:
        v = m.get(k)
        items.append({
            "key": f"model.{k}",
            "label": f"model.{k}",
            "preview": str(v) if v not in (None, "") else "(unset)",
            "chars": len(str(v or "")),
            "meta": {"value": v, "unset": v in (None, "")},
        })

    fb = d.get("fallback_providers")
    if isinstance(fb, list):
        names = [f"{x.get('provider', '?')}:{x.get('model', '?')}" if isinstance(x, dict) else str(x)
                 for x in fb]
        items.append({
            "key": "fallback_providers",
            "label": "fallback_providers",
            "preview": " · ".join(names) if names else "(none)",
            "chars": len(names),
            "meta": {"value": copy.deepcopy(fb), "count": len(names)},
        })
    else:
        items.append({"key": "fallback_providers", "label": "fallback_providers",
                      "preview": "(none)", "chars": 0, "meta": {"value": [], "count": 0}})

    return {"ok": True, "items": items, "provider": m.get("provider"),
            "default": m.get("default")}


def push(src_dir: Path, dest_dir: Path, *, picks: list[str] | None = None,
         dry_run: bool = False) -> dict:
    """Write the anchor's SELECTION into a member, touching only the keys this layer owns.

    `picks` narrows which settings move; `None` means all of them. Every other key in the
    member's config.yaml survives untouched — that is the whole reason this is not a file
    copy.
    """
    src = read_selection(src_dir)
    if not src.get("ok"):
        return {"ok": False, "error": src.get("error"), "unchanged": False}

    if dry_run:
        return {"ok": True, "action": "would-push", "dry_run": True,
                "picked": None if picks is None else len(picks), "unchanged": False}

    try:
        cur = _load(dest_dir)
    except Exception as e:
        return {"ok": False, "error": f"member config.yaml unreadable: {type(e).__name__}",
                "unchanged": False}

    want = None if picks is None else set(picks)
    before = yaml.safe_dump(cur, sort_keys=False)

    sel = {i["key"]: i["meta"].get("value") for i in src["items"]}

    model = cur.get("model")
    if not isinstance(model, dict):
        model = {}
    for k in OWNED:
        path = f"model.{k}"
        if want is not None and path not in want:
            continue
        if sel.get(path) not in (None, ""):
            model[k] = sel[path]
    if model:
        cur["model"] = model

    if want is None or "fallback_providers" in want:
        fb = sel.get("fallback_providers")
        if fb:
            cur["fallback_providers"] = copy.deepcopy(fb)
        elif want is not None:
            cur.pop("fallback_providers", None)

    after = yaml.safe_dump(cur, sort_keys=False)
    if after == before:
        # Identical content is NOT rewritten: a no-op write litters a snapshot and bumps the
        # mtime, which makes "nothing to sync" indistinguishable from "synced".
        return {"ok": True, "unchanged": True, "path": str(config_path(dest_dir))}

    p = config_path(dest_dir)
    existed = p.exists()
    snap = apply_mod.snapshot(p) if existed else None
    p.parent.mkdir(parents=True, exist_ok=True)
    # Atomic: the running gateway reads this file. A partial write would leave it unparseable.
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(after)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    return {"ok": True, "unchanged": False, "path": str(p), "snapshot": snap,
            "created": not existed,
            "selection": {"provider": (cur.get("model") or {}).get("provider"),
                          "default": (cur.get("model") or {}).get("default")}}
