"""R-015 transcript-medium pre-filter (Change A, Iter-2 Strand 1).

Per spec § 4 R-015 (v1.6 with SD-2-011): raw transcripts carry medium-of-record
noise (tool-call envelopes, code blocks, JSON payloads, URLs, file paths, YAML
config) that is not spec-equivalent text. This noise inflates Cat 3 (over-
emphasis) and Cat 6 (terminology drift) false positives. The pre-filter strips
it before those two detectors run.

Scope (SD-2-011):
  - Applies ONLY to manifest entries of type `session` or `transcript-ref`.
    `outbox` / `brief` / `pr-diff` are not transcript-medium and pass through
    unchanged.
  - Tool-call stripping has TWO mandatory paths:
      Path 1 (structured) — JSONL tool invocations identified by
        `content[].type == 'tool_use'` (and `tool_result`) in assistant/user
        turn content blocks (F-2-007 / F-2-009 Claude Code JSONL structure).
      Path 2 (XML literal) — `<function_calls>...</function_calls>` and
        `<tool_use>...</tool_use>` appearing as literal text in assistant text
        content (separate from Path 1).
  - Also strips: code fences, URLs, absolute + relative file paths, YAML config
    blocks.

Out of scope (DQ-2, logged for Iter-3): bare-hostname URL detection
(`host.example.co.uk:8080` style) — see design-review § 9 item 15.
"""

from __future__ import annotations

import json
import re

TRANSCRIPT_TYPES = ("session", "transcript-ref")

# --- regex strips (applied to text, after any JSONL reconstruction) ----------

_CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_FUNCTION_CALLS_RE = re.compile(
    r"<function_calls>.*?</function_calls>", re.DOTALL | re.IGNORECASE
)
_TOOL_USE_RE = re.compile(
    r"<tool_use>.*?</tool_use>", re.DOTALL | re.IGNORECASE
)
_URL_RE = re.compile(r"https?://\S+")
# Path tokens: optional leading ./ or ../, then an absolute or relative path
# with at least one slash. Anchored at a non-whitespace boundary so we don't
# eat "and/or" mid-word, and require a slash so bare words survive.
_PATH_RE = re.compile(r"(?<!\S)\.{0,2}/[A-Za-z0-9_.\-/]+")
# A run of >=3 consecutive top-level `key: value` lines is treated as a YAML
# config block (code fences are already gone by the time this runs).
_YAML_LINE_RE = re.compile(r"^\s{0,3}[A-Za-z_][\w.\-]*:\s+\S")


def _looks_like_jsonl(body: str) -> bool:
    """Heuristic: first non-blank line is a single JSON object."""
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        return s.startswith("{") and s.endswith("}")
    return False


def _texts_from_message(obj: dict) -> list[str]:
    """Extract human-readable text from one Claude Code JSONL message object.

    Keeps `text`-type content blocks from user/assistant turns; drops
    `tool_use` / `tool_result` / `thinking` / image blocks (Path 1). Whole
    tool-result messages (top-level `type == 'tool_result'` or `role == 'tool'`)
    are dropped.
    """
    texts: list[str] = []
    top_type = obj.get("type")
    msg = obj.get("message")
    if isinstance(msg, dict):
        role = msg.get("role")
        content = msg.get("content")
    else:
        role = obj.get("role")
        content = obj.get("content")

    if top_type == "tool_result" or role == "tool":
        return texts

    if isinstance(content, str):
        texts.append(content)
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                texts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            # tool_use / tool_result / thinking / image blocks dropped
    return texts


def _strip_jsonl_tool_calls(body: str) -> str:
    """Path 1: reconstruct plain text from JSONL, dropping tool blocks.

    Non-JSON lines are preserved verbatim so Path 2 + the regex strips can act
    on them.
    """
    out: list[str] = []
    for line in body.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except (ValueError, TypeError):
            out.append(line)
            continue
        if isinstance(obj, dict):
            out.extend(_texts_from_message(obj))
        else:
            out.append(line)
    return "\n".join(out)


def _strip_yaml_blocks(text: str) -> str:
    """Drop runs of >=3 consecutive top-level `key: value` lines.

    Shorter runs are kept — an isolated `key: value` line is usually prose
    (e.g. "Decision: ship it"), not a config block.
    """
    lines = text.splitlines()
    keep: list[str] = []
    pending: list[str] = []
    for line in lines:
        if _YAML_LINE_RE.match(line):
            pending.append(line)
        else:
            if len(pending) < 3:
                keep.extend(pending)
            pending = []
            keep.append(line)
    if len(pending) < 3:
        keep.extend(pending)
    return "\n".join(keep)


def prefilter_transcript(body: str) -> str:
    """Apply the full R-015 dual-path pre-filter to one transcript body."""
    if not body:
        return body
    text = body
    # Path 1: structured JSONL tool-call removal (only if it looks like JSONL).
    if _looks_like_jsonl(text):
        text = _strip_jsonl_tool_calls(text)
    # Code fences first — removes fenced tool dumps / JSON / YAML wholesale.
    text = _CODE_FENCE_RE.sub(" ", text)
    # Path 2: XML-literal tool-call envelopes.
    text = _FUNCTION_CALLS_RE.sub(" ", text)
    text = _TOOL_USE_RE.sub(" ", text)
    # URLs, then path tokens (URLs first so we don't mangle the scheme slash).
    text = _URL_RE.sub(" ", text)
    text = _PATH_RE.sub(" ", text)
    # YAML config blocks that weren't fenced.
    text = _strip_yaml_blocks(text)
    return text


def build_filtered_bodies(manifest, bodies: dict[int, str]) -> dict[int, str]:
    """Return a body dict with transcript-medium entries pre-filtered.

    Non-transcript entries (`outbox` / `brief` / `pr-diff`) are passed through
    unchanged. Used to feed Cat 3 + Cat 6 a denoised view while Cat 1/2/4/5 see
    the original bodies.
    """
    out: dict[int, str] = {}
    for entry in manifest:
        body = bodies.get(entry.index, "")
        if entry.type in TRANSCRIPT_TYPES:
            out[entry.index] = prefilter_transcript(body)
        else:
            out[entry.index] = body
    return out
