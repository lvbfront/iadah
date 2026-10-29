"""Review learned runbooks. Learned runbooks are saved as `pending_review` and are never applied
automatically until approved here.

uv run python -m agent.runbooks list
uv run python -m agent.runbooks approve <name-or-file>
"""
import sys
import time
from pathlib import Path

import yaml

import config


def _all():
    for p in sorted(Path(config.RUNBOOKS_DIR).glob("*.yaml")):
        yield p, yaml.safe_load(p.read_text())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "list":
        for p, rb in _all():
            acts = ", ".join(f"{a['action']}({a['service']})" for a in rb["actions"])
            print(f"{rb.get('status', 'pending_review'):15} {p.name:40} {acts}  [{rb.get('source', '')}]")
    elif cmd == "approve" and len(sys.argv) == 3:
        target = sys.argv[2].removesuffix(".yaml")
        for p, rb in _all():
            if target in (p.stem, rb.get("name")):
                rb["status"], rb["approved_at"] = "approved", time.strftime("%Y-%m-%d %H:%M:%S")
                p.write_text(yaml.safe_dump(rb, sort_keys=False))
                print(f"approved {p.name}")
                break
        else:
            sys.exit(f"no runbook named {target}")
    else:
        sys.exit(__doc__)
