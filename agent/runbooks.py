"""Review learned runbooks. Learned runbooks are saved as `pending_review` and are never applied
automatically until approved here.

uv run python -m agent.runbooks list
uv run python -m agent.runbooks approve <name-or-file>
uv run python -m agent.runbooks hide <seed>      # learning demo: take a seed out of runbooks/ temporarily
uv run python -m agent.runbooks restore          # put hidden seeds back
uv run python -m agent.runbooks delete <learned> # drop a learned runbook (seeds are never deleted)
"""
import shutil
import sys
import time
from pathlib import Path

import yaml

import config

HIDDEN = Path(config.STATE_DIR) / "hidden_runbooks"


def _all():
    for p in sorted(Path(config.RUNBOOKS_DIR).glob("*.yaml")):
        yield p, yaml.safe_load(p.read_text())


def _find(target: str):
    target = target.removesuffix(".yaml")
    for p, rb in _all():
        if target in (p.stem, rb.get("name")):
            return p, rb
    raise ValueError(f"no runbook named {target}")


def listing() -> list[dict]:
    rows = [{"file": p.name, "status": rb.get("status", "pending_review"), "seed": rb.get("source") == "seed",
             "types": rb.get("signature", {}).get("incident_types", []), "times_used": rb.get("times_used", 0),
             "actions": [f"{a['action']}({a['service']})" for a in rb["actions"]], "hidden": False}
            for p, rb in _all()]
    return rows + [{"file": p.name, "status": "hidden", "seed": True, "types": [], "times_used": 0, "actions": [],
                    "hidden": True} for p in sorted(HIDDEN.glob("*.yaml"))]


def approve(target: str) -> str:
    p, rb = _find(target)
    rb["status"], rb["approved_at"] = "approved", time.strftime("%Y-%m-%d %H:%M:%S")
    p.write_text(yaml.safe_dump(rb, sort_keys=False))
    return p.name


def hide(target: str) -> str:
    p, rb = _find(target)
    if rb.get("source") != "seed":
        raise ValueError(f"{p.name} is not a seed runbook")
    HIDDEN.mkdir(parents=True, exist_ok=True)
    shutil.move(p, HIDDEN / p.name)
    return p.name


def restore() -> list[str]:
    names = [p.name for p in HIDDEN.glob("*.yaml")]
    for n in names:
        shutil.move(HIDDEN / n, Path(config.RUNBOOKS_DIR) / n)
    return names


def delete(target: str) -> str:
    p, rb = _find(target)
    if not p.name.endswith(".learned.yaml"):
        raise ValueError(f"{p.name} is not a learned runbook")
    p.unlink()
    return p.name


if __name__ == "__main__":
    cmd, arg = (sys.argv[1:] + [None, None])[:2]
    done = {"approve": ("approved", approve), "hide": ("hidden", hide), "delete": ("deleted", delete)}
    try:
        if cmd in (None, "list"):
            for r in listing():
                print(f"{r['status']:15} {r['file']:40} {', '.join(r['actions'])}")
        elif cmd in done and arg:
            print(f"{done[cmd][0]} {done[cmd][1](arg)}")
        elif cmd == "restore":
            print(f"restored {restore() or 'nothing'}")
        else:
            sys.exit(__doc__)
    except ValueError as e:
        sys.exit(str(e))
