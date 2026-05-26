"""Source-anchor records (per spec § R-003 + SD-008).

Two forms:
  - file:   {type: "file",   path: str, anchor: str}
  - qdrant: {type: "qdrant", collection: str, point_id: str,
             payload_field: Optional[str]}

Anchor `anchor` on file types: a line-range token like "L42" / "L42-L57" or a
section header like "§ Glossary".
"""

from __future__ import annotations

from typing import Optional


def file_anchor(path: str, anchor: str) -> dict:
    return {"type": "file", "path": path, "anchor": anchor}


def qdrant_anchor(collection: str, point_id: str,
                  payload_field: Optional[str] = None) -> dict:
    a: dict = {"type": "qdrant", "collection": collection,
               "point_id": point_id}
    if payload_field:
        a["payload_field"] = payload_field
    return a


def render_anchor_markdown(anchor: dict) -> str:
    """Render an anchor as a Markdown link string."""
    if anchor.get("type") == "file":
        path = anchor["path"]
        a = anchor.get("anchor") or ""
        if a.startswith("L"):
            # GitHub-style line-range fragment.
            return f"[{path}#{a}]({path}#{a})"
        if a:
            return f"[{path} ({a})]({path})"
        return f"[{path}]({path})"
    if anchor.get("type") == "qdrant":
        coll = anchor["collection"]
        pid = anchor["point_id"]
        field = anchor.get("payload_field")
        suffix = f" :: {field}" if field else ""
        return f"`qdrant://{coll}/{pid}{suffix}`"
    return repr(anchor)
