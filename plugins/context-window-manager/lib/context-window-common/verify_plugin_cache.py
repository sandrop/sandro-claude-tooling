"""Flag versioned plugin cache paths a handoff cites that an upgrade superseded.

extract_plugin_cache_refs(text) -> [{"path", "marketplace", "plugin", "version"}]
check_plugin_cache_refs(refs, installed_json=None) -> {"stale": [...], "notes": [...]}

A stale entry is {"path", "cited_version", "installed_version", "installed_path"}.
The scan runs over the whole handoff text for the cache marker plus three
segments (marketplace, plugin, digit-led version). The reported path is
best-effort: it runs from the previous stop character to the next one.
Each walk back stops at the previous match, so the scan stays linear.
"""

import json
import re
from pathlib import Path

# Characters that end a path: whitespace, quotes, backtick, shell operators
# and the ":" path-list separator.
_STOP = frozenset(" \t\r\n\f\v\"'`;&|<>():")
# The version segment must start with a digit so `<version>` placeholders in
# docs do not match.
_REF_RE = re.compile(
    r"""/\.claude/plugins/cache/([^/\s"'`;&|<>():]+)/([^/\s"'`;&|<>():]+)/"""
    r"""(\d[^/\s"'`;&|<>():]*)[^\s"'`;&|<>():]*"""
)


# ponytail: path stops at whitespace or quotes, so a quoted ancestor with
# spaces is truncated and two markers with no separator merge; detection keys
# on the three segments, not the path.
def _scan(text: str):
    lo = 0
    for m in _REF_RE.finditer(text):
        s = m.start()
        while s > lo and text[s - 1] not in _STOP:
            s -= 1
        lo = m.end()
        yield {
            "path": text[s : m.end()],
            "marketplace": m[1],
            "plugin": m[2],
            "version": m[3],
        }


def extract_plugin_cache_refs(text: str) -> list:
    refs, seen = [], set()
    for ref in _scan(text):
        if ref["path"] in seen:
            continue
        seen.add(ref["path"])
        refs.append(ref)
    return refs


def _load_plugins(installed_json):
    # Returns (plugins dict, None) or (None, note). Never raises: a resume
    # report must not fail on a missing or odd installed_plugins.json.
    try:
        data = json.loads(Path(installed_json).read_text())
    except (OSError, ValueError) as exc:
        return None, f"could not read {installed_json}: {exc}"
    plugins = data.get("plugins") if isinstance(data, dict) else None
    if not isinstance(plugins, dict):
        return None, f"{installed_json} has no 'plugins' object"
    return plugins, None


def _usable_record(record) -> bool:
    # A record must carry a non-empty string version and installPath; anything
    # else would raise on comparison or yield a stale entry with null fields.
    if not isinstance(record, dict):
        return False
    for field in ("version", "installPath"):
        value = record.get(field)
        if not isinstance(value, str) or not value:
            return False
    return True


def check_plugin_cache_refs(refs: list, installed_json=None) -> dict:
    if not refs:
        return {"stale": [], "notes": []}
    if installed_json is None:
        installed_json = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
    plugins, note = _load_plugins(installed_json)
    if plugins is None:
        return {"stale": [], "notes": [note]}
    stale, notes = [], []
    for ref in refs:
        key = f"{ref['plugin']}@{ref['marketplace']}"
        records = plugins.get(key)
        if not isinstance(records, list) or not records:
            notes.append(f"{key} is not installed; cited {ref['path']}")
            continue
        if not all(_usable_record(r) for r in records):
            notes.append(f"{key} has malformed install records; cited {ref['path']}")
            continue
        if ref["version"] not in {r["version"] for r in records}:
            stale.append(
                {
                    "path": ref["path"],
                    "cited_version": ref["version"],
                    "installed_version": records[0]["version"],
                    "installed_path": records[0]["installPath"],
                }
            )
    return {"stale": stale, "notes": notes}
