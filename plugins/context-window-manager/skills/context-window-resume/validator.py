#!/usr/bin/env python3
# validator.py - context-window-resume pre-flight + drift/verify report.
import json
import sys
from pathlib import Path

COMMON = Path(__file__).resolve().parent.parent / "context-window-common"
sys.path.insert(0, str(COMMON))
import context_schema  # noqa: E402
import git_drift  # noqa: E402
import verify_refs  # noqa: E402


def _newest_context(cwd: Path):
    d = context_schema.save_dir(cwd)
    if not d.is_dir():
        return None
    files = sorted(d.glob(context_schema.CONTEXT_GLOB))
    return files[-1] if files else None


def main():
    cwd = Path.cwd()
    checks, report, errors = {"context_file": None}, None, []
    try:
        newest = _newest_context(cwd)
        if newest is not None:
            checks["context_file"] = newest.name
            text = newest.read_text(encoding="utf-8", errors="ignore")
            meta = context_schema.parse_metadata_block(text)
            drift = git_drift.compute_drift(
                meta["git_sha"], meta["git_branch"], str(cwd)
            )
            refs = verify_refs.verify_refs(verify_refs.extract_refs(text), str(cwd))
            report = {"meta": meta, "drift": drift, "refs": refs}
    except Exception as e:  # noqa: BLE001 - surface any failure via errors/ok
        errors.append(f"{type(e).__name__}: {e}")
    print(
        json.dumps(
            {"ok": not errors, "checks": checks, "report": report, "errors": errors},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
