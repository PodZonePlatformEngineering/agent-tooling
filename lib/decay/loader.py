"""Artefact content loading.

File-type entries are read directly from disk (path resolved against the
manifest's parent directory if relative). Qdrant-type entries are loaded via
`lib.sessions_reader`; if no API key is configured, the loader returns an
empty body and the runner emits a "qdrant unreachable" coverage note rather
than failing the whole run (operator manifest may mix file + qdrant entries).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from .manifest import ManifestEntry


class ArtefactLoader:

    def __init__(self, manifest_dir: Optional[Path] = None) -> None:
        self.manifest_dir = manifest_dir
        self._qdrant_warned = False
        self._qdrant_payloads: dict[tuple[str, str], str] = {}

    def _resolve_file(self, raw_path: str) -> Path:
        p = Path(raw_path)
        if p.is_absolute():
            return p
        if self.manifest_dir is not None:
            return (self.manifest_dir / p).resolve()
        return p.resolve()

    def load(self, entry: ManifestEntry) -> str:
        if entry.is_file():
            assert entry.path is not None
            fp = self._resolve_file(entry.path)
            if not fp.exists():
                return ""
            try:
                return fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return ""
        if entry.is_qdrant():
            return self._load_qdrant(
                entry.qdrant_collection or "", entry.qdrant_id or ""
            )
        return ""

    def _load_qdrant(self, collection: str, point_id: str) -> str:
        key = (collection, point_id)
        if key in self._qdrant_payloads:
            return self._qdrant_payloads[key]
        body = ""
        try:
            if not os.environ.get("PODZONE_QDRANT_APIKEY"):
                if not self._qdrant_warned:
                    print(
                        "[decay-detector] PODZONE_QDRANT_APIKEY not set; "
                        "Qdrant-referenced artefacts will be empty",
                        file=sys.stderr,
                    )
                    self._qdrant_warned = True
            else:
                from lib import sessions_reader  # type: ignore
                body = sessions_reader.fetch_point_body(collection, point_id)
        except Exception as exc:  # noqa: BLE001
            if not self._qdrant_warned:
                print(
                    f"[decay-detector] Qdrant fetch failed ({exc}); "
                    f"continuing with empty body",
                    file=sys.stderr,
                )
                self._qdrant_warned = True
            body = ""
        self._qdrant_payloads[key] = body
        return body
