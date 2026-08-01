"""extraction_scan.py — the mechanical half of the extraction gate (PROJ-011/T-126).

Control of record: ``podzoneTeam/planning/projects/PROJ-011-academy/
session-to-curriculum-extraction-gate.md`` (Athena, PROJ-011/T-124). This module
enforces what is mechanically checkable in that document and deliberately nothing
else — the declaring agent remains the owner (gate §3), the scanner is second line.

Three tiers, in the priority order of the T-124 §P1 spec:

* **Tier 1 — hard fail, high precision.** Class A shapes that need no judgement:
  SA ID numbers (checksum-validated), passport identifiers in a passport context,
  contact details, and credential material. Credentials are the Category 5 case:
  the likeliest way a trainee's real system leaks is a config fragment pasted to
  illustrate a point.
* **Tier 2 — structural, 100% precision, the highest-value check.** Any file
  written under a known extraction destination must carry a well-formed §7
  declaration whose boundaries cover the destination and stay inside what the
  brief authorised. It does not guess at content, so it cannot be wrong about it:
  absence is a one-line grep.
* **Tier 3 — warn, never fail.** Precision is too low to block on. Destination-aware
  by construction: **silent on Class P at B2**, because a control that cries wolf on
  every planning document is disabled within a week, and a disabled control is worse
  than none.

Two invariants worth stating because the rest of the module depends on them:

1. **Tiers 1 and 3 run over added lines only** (the extract), while tier 2 runs over
   the whole file (the artefact must carry the declaration). Scanning whole files for
   content would re-report every pre-existing line in a touched document, which is the
   noise failure mode above.
2. **Nothing matched is ever echoed in full.** Findings carry a masked excerpt; a
   scanner that prints the secret it found has published it into CI logs.

The stated limit, per gate §9, repeated here so it is not lost in the tooling: a
declaration detects **omission, not falsification**, and a clean scan does not mean a
clean extract.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

BOUNDARIES = ("B1", "B2", "B3", "B4")

BOUNDARY_LABELS = {
    "B1": "curriculum",
    "B2": "podzoneTeam planning",
    "B3": "another trainee",
    "B4": "shared substrate",
}

GATE_DOC = "PROJ-011-academy/session-to-curriculum-extraction-gate.md"

#: Boundaries at which participant identity (Class P) must be removed. B2 is
#: deliberately absent — attribution is the point there (gate §2.1).
CLASS_P_STRICT = ("B1", "B3", "B4")

TIER_HARD = 1
TIER_STRUCTURAL = 2
TIER_WARN = 3

_DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "data" / "extraction-destinations.json"

# Text extensions worth reading. Anything else is skipped rather than guessed at.
TEXT_SUFFIXES = {
    ".md", ".markdown", ".txt", ".rst", ".yaml", ".yml", ".json", ".toml",
    ".py", ".sh", ".bash", ".ts", ".tsx", ".js", ".jsx", ".sql", ".env", ".cfg",
    ".ini", ".html", ".css", ".csv",
}


class ExtractionScanError(RuntimeError):
    """Configuration or usage error — distinct from a scan finding."""


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #

@dataclass
class Finding:
    tier: int
    code: str
    path: str
    line: int
    message: str
    excerpt: str = ""
    boundary: Optional[str] = None

    def format(self) -> str:
        where = f"{self.path}:{self.line}" if self.line else self.path
        bits = [f"[tier {self.tier}] {self.code}", where, self.message]
        if self.excerpt:
            bits.append(f"({self.excerpt})")
        return " · ".join(bits)


def mask(value: str, keep: int = 3) -> str:
    """Mask a matched value. Never echo what was found — CI logs are forever."""
    value = value.strip()
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * min(len(value) - keep, 12)


# --------------------------------------------------------------------------- #
# Destination configuration — which paths are which boundary
# --------------------------------------------------------------------------- #

def _glob_to_regex(pattern: str) -> re.Pattern:
    """Translate a path glob to a regex.

    ``**`` crosses directory separators, ``*`` and ``?`` do not. Python's fnmatch
    is not usable here precisely because its ``*`` crosses ``/``, which would make
    every destination pattern match far more than it names.
    """
    out = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                if pattern[i:i + 1] == "/":
                    i += 1
                    out.append("")
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$")


@dataclass
class DestinationConfig:
    rules: list = field(default_factory=list)      # [(boundary, compiled, glob)]
    exempt: list = field(default_factory=list)     # [compiled]
    declaration_suffixes: tuple = (".md", ".markdown")

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "DestinationConfig":
        path = Path(path) if path else _DEFAULT_CONFIG
        if not path.exists():
            raise ExtractionScanError(f"destination config not found: {path}")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ExtractionScanError(f"destination config is not valid JSON: {exc}") from exc

        rules = []
        for entry in raw.get("destinations", []):
            boundary = entry.get("boundary")
            if boundary not in BOUNDARIES:
                raise ExtractionScanError(f"unknown boundary in config: {boundary!r}")
            for glob in entry.get("match", []):
                rules.append((boundary, _glob_to_regex(glob), glob))
        exempt = [_glob_to_regex(g) for g in raw.get("exempt", [])]
        suffixes = tuple(raw.get("declaration_suffixes", (".md", ".markdown")))
        return cls(rules=rules, exempt=exempt, declaration_suffixes=suffixes)

    def boundaries_for(self, path: str) -> list:
        """Every boundary whose destination patterns claim this path.

        More than one can match — a file can be both curriculum and substrate-bound —
        and the strictest governs (gate §6, first checklist line).
        """
        norm = str(path).replace(os.sep, "/").lstrip("./")
        if any(rx.match(norm) for rx in self.exempt):
            return []
        hits = []
        for boundary, rx, _glob in self.rules:
            if boundary not in hits and rx.match(norm):
                hits.append(boundary)
        return sorted(hits)

    def needs_declaration(self, path: str) -> bool:
        return Path(path).suffix.lower() in self.declaration_suffixes


# --------------------------------------------------------------------------- #
# Roster — participant identity, held in one maintained file, never hardcoded
# --------------------------------------------------------------------------- #

@dataclass
class Roster:
    names: tuple = ()
    emails: tuple = ()
    source: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.names or self.emails)

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Roster":
        """Load the participant roster.

        Resolution order: explicit ``path`` → ``$EXTRACTION_ROSTER`` → unconfigured.

        There is deliberately **no default roster inside this repository**:
        ``agent-tooling`` is public, and a maintained list of real participant names
        is exactly the Class P material the gate exists to keep out of public reach.
        The real roster lives in a private repo (``podzoneTeam``); an unconfigured
        roster disables the tier-3 name check and says so, rather than shipping names
        here to make a warning tier work.

        ``.md``/``.markdown`` files are parsed as the operator-maintained roster
        (PROJ-011/T-129) — three pipe tables plus prose, not a machine-readable
        format kept in lockstep by hand. Anything else is read as the JSON shape
        (``{"names": [...], "emails": [...]}``) this module has always accepted.

        A configured path that does not resolve is a **hard failure**, not a silent
        fallback to unconfigured: the roster lives in the same checkout as the
        content being scanned, so "unreachable" here means misconfiguration (a typo,
        a deleted file), never unavailability. A gate that quietly disarms itself
        when its roster goes missing is worse than one that never had a roster.
        """
        raw_path = path or os.environ.get("EXTRACTION_ROSTER") or ""
        if not raw_path:
            return cls()
        p = Path(raw_path).expanduser()
        if not p.exists():
            raise ExtractionScanError(
                f"roster configured but unreachable: {p} — this is a misconfiguration "
                "(path typo, deleted file), not an absent roster; fix the path or "
                "unset EXTRACTION_ROSTER rather than let the gate degrade silently")
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in (".md", ".markdown"):
            names, emails = _parse_roster_markdown(text)
        else:
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ExtractionScanError(f"roster is not valid JSON: {exc}") from exc
            names = tuple(sorted({str(n).strip() for n in data.get("names", []) if str(n).strip()}))
            emails = tuple(sorted({str(e).strip().lower() for e in data.get("emails", []) if str(e).strip()}))
        return cls(names=names, emails=emails, source=str(p))

    def is_participant_email(self, address: str) -> bool:
        return address.strip().lower() in self.emails


_RE_MD_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_RE_MD_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_RE_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)\s*$")


def _parse_roster_markdown(text: str) -> tuple:
    """Parse the operator-maintained roster's pipe tables (PROJ-011/T-129).

    Every table with an ``Email`` column contributes its addresses — the roster's own
    "Scanner guidance" section states that all three tables (testing cohort, operator
    aliases, invited-not-yet-signed-up) are Class P. Only tables with a ``Participant``
    column contribute names: the operator-aliases table names roles ("Operator (git
    author)"), not people, and role words make poor name-match patterns, so that table
    is email-only by construction (no ``Participant`` header, nothing to collect).

    Generating a parser around the settled markdown — rather than asking the operator
    to hand-maintain a second, machine-readable file — is the point of this function;
    see brief PROJ-011/T-129 Task 1.2.
    """
    names: set = set()
    emails: set = set()
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        header = _RE_MD_TABLE_ROW.match(lines[i])
        if header and i + 1 < len(lines) and _RE_MD_TABLE_SEP.match(lines[i + 1]):
            headers = [c.strip().lower() for c in header.group(1).split("|")]
            email_col = next((idx for idx, h in enumerate(headers) if "email" in h), None)
            name_col = next((idx for idx, h in enumerate(headers) if "participant" in h), None)
            i += 2
            while i < len(lines):
                row = _RE_MD_TABLE_ROW.match(lines[i])
                if not row:
                    break
                cells = [c.strip() for c in row.group(1).split("|")]
                if email_col is not None and email_col < len(cells):
                    match = _RE_EMAIL.search(cells[email_col])
                    if match:
                        emails.add(match.group(1).strip().lower())
                if name_col is not None and name_col < len(cells):
                    name = _RE_TRAILING_PAREN.sub("", cells[name_col]).strip()
                    if name:
                        names.add(name)
                i += 1
            continue
        i += 1
    return tuple(sorted(names)), tuple(sorted(emails))


# --------------------------------------------------------------------------- #
# Tier 1 — hard-fail patterns
# --------------------------------------------------------------------------- #

_RE_LONG_DIGITS = re.compile(r"(?<![\d-])(\d{13})(?![\d-])")
_RE_PASSPORT = re.compile(r"\b([A-Z]{1,2}\d{6,9})\b")
_RE_PASSPORT_CONTEXT = re.compile(r"passport", re.IGNORECASE)
_RE_EMAIL = re.compile(r"\b([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b")
# A separator (or the +27 country code) is required. Bare ten-digit runs are not
# distinguishable from identifiers, and a planning corpus is full of those — the
# unseparated form was the single largest source of false positives in the
# acceptance run over podzoneTeam history. Precision beats recall at tier 1.
_RE_PHONE_SA = re.compile(
    r"(?<![\w+])(?:\+27[\s-]?\d{2}|0\d{2})[\s-]\d{3}[\s-]?\d{4}(?![\w])"
    r"|(?<![\w+])\+27[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{4}(?![\w])")
_RE_PHONE_INTL = re.compile(r"(?<![\w+])\+(?!27)\d{1,3}(?:[\s-]?\d){7,12}(?![\w])")

_CREDENTIAL_PATTERNS = (
    ("CREDENTIAL_ANTHROPIC", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
    ("CREDENTIAL_OPENAI", re.compile(r"\bsk-[A-Za-z0-9]{32,}")),
    ("CREDENTIAL_GITHUB", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("CREDENTIAL_GITHUB_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("CREDENTIAL_AWS_KEY_ID", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("CREDENTIAL_SLACK", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("CREDENTIAL_PRIVATE_KEY", re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")),
    ("CREDENTIAL_JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("CREDENTIAL_CONNECTION_STRING",
     re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:([^\s/@]+)@[^\s/]+")),
)

#: Values that look like credentials but are placeholders. A scanner that fails on
#: documentation examples gets bypassed with ``--no-verify`` and then ignored.
_PLACEHOLDER_HINTS = (
    "example", "placeholder", "redacted", "your-", "your_", "changeme", "change-me",
    "xxxx", "<", "{{", "${", "…", "...", "****", "dummy", "fake", "sample", "n/a",
)


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.lower()
    return any(hint in lowered for hint in _PLACEHOLDER_HINTS)


#: Email-shaped strings that are not participant addresses, ignored by **pattern**
#: rather than by roster entry (roster "Scanner guidance", PROJ-011/T-129): the two
#: documentation placeholder domains, and the ``git@host`` form left by pasted SSH
#: remote URLs (``git@github.com:org/repo.git``). Placeholders are infinite; a roster
#: enumerates people, not syntax, so these never belong in it.
_RE_EMAIL_IGNORE = re.compile(r"@(?:example|customer)\.com$|^git@", re.IGNORECASE)


def _is_ignored_email(address: str) -> bool:
    return bool(_RE_EMAIL_IGNORE.search(address))


#: The gate's own placeholder convention (§5): [CLAIMANT_A], [ID_001], [PHONE_001].
_RE_PLACEHOLDER_TOKEN = re.compile(r"\[[A-Z][A-Z0-9_]{2,}\]")


def is_demonstration_line(line: str) -> bool:
    """A line that pairs a raw shape with its §5 placeholder is teaching the rule.

    The gate document and its predecessor both tabulate "example of real data →
    placeholder", so a scanner without this fires on the very documents that define
    the convention — and the first thing anyone does with a control that fails its
    own specification is stop trusting it.

    A deliberate, narrow recall trade, and it is stated: a real extract that replaced
    some entities on a line but left one raw beside them is not caught here. It applies
    to PII shapes only — credential patterns are never suppressed by it, because a
    committed secret next to a placeholder is still a committed secret.
    """
    return bool(_RE_PLACEHOLDER_TOKEN.search(line))


def luhn_ok(digits: str) -> bool:
    """Luhn check — the SA ID check digit algorithm.

    This is what keeps the 13-digit rule from firing on every long number in a
    document; without it the pattern is unusable and gets turned off.
    """
    total = 0
    for index, ch in enumerate(reversed(digits)):
        value = int(ch)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _valid_sa_id_date(digits: str) -> bool:
    """First six digits of an SA ID are YYMMDD — a cheap second discriminator."""
    month = int(digits[2:4])
    day = int(digits[4:6])
    return 1 <= month <= 12 and 1 <= day <= 31


def is_sa_id(digits: str) -> bool:
    return len(digits) == 13 and digits.isdigit() and _valid_sa_id_date(digits) and luhn_ok(digits)


def scan_line_tier1(line: str, *, roster: Roster, boundaries: Sequence[str]) -> list:
    """Tier-1 findings for one line. Returns ``(code, message, excerpt, tier)`` tuples."""
    out = []
    demo = is_demonstration_line(line)

    for match in _RE_LONG_DIGITS.finditer(line):
        if demo:
            break
        digits = match.group(1)
        if is_sa_id(digits):
            out.append(("PII_SA_ID", "13-digit identifier passes the SA ID checksum",
                        mask(digits), TIER_HARD))

    if _RE_PASSPORT_CONTEXT.search(line) and not demo:
        for match in _RE_PASSPORT.finditer(line):
            value = match.group(1)
            if not _looks_like_placeholder(value):
                out.append(("PII_PASSPORT", "passport-shaped identifier in a passport context",
                            mask(value), TIER_HARD))

    for match in _RE_EMAIL.finditer(line):
        address = match.group(1)
        if (demo or _looks_like_placeholder(address)
                or address.lower().endswith(".example") or _is_ignored_email(address)):
            continue
        strict = any(b in CLASS_P_STRICT for b in boundaries)
        if roster.is_participant_email(address):
            # Class P: attribution is the point at B2 (gate §2.1), a leak elsewhere.
            if strict:
                out.append(("PARTICIPANT_EMAIL",
                            "participant email address at a boundary that removes Class P",
                            mask(address), TIER_HARD))
            continue
        if not strict and not roster.configured:
            # B2 only, with no roster to classify against. The gate permits Class P
            # here, and without a roster a participant's address is indistinguishable
            # from a client's — so blocking would be a guess, and a wrong guess at
            # tier 1 blocks a legitimate planning document. Warn instead, and say why.
            # Configuring a roster promotes this back to a hard fail for Class A.
            out.append(("EMAIL_UNCLASSIFIED",
                        "email address at B2 with no roster configured — cannot tell "
                        "Class A from Class P; configure --roster to enforce",
                        mask(address), TIER_WARN))
            continue
        out.append(("PII_EMAIL", "email address (Class A unless rostered)",
                    mask(address), TIER_HARD))

    for rx, code in ((_RE_PHONE_SA, "PII_PHONE_SA"), (_RE_PHONE_INTL, "PII_PHONE_INTL")):
        for match in rx.finditer(line):
            value = match.group(0)
            if demo or _looks_like_placeholder(value):
                continue
            out.append((code, "telephone-number shape", mask(value), TIER_HARD))

    for code, rx in _CREDENTIAL_PATTERNS:
        for match in rx.finditer(line):
            value = match.group(1) if match.groups() else match.group(0)
            if _looks_like_placeholder(value):
                continue
            out.append((code, "credential or secret material (Category 5 — live systems)",
                        mask(value), TIER_HARD))

    return out


# --------------------------------------------------------------------------- #
# Tier 3 — warnings, destination-aware
# --------------------------------------------------------------------------- #

_RE_MONEY = re.compile(r"(?:R|ZAR|\$|£|€|USD|GBP|EUR)\s?((?:\d{1,3}(?:[ ,]\d{3})+|\d{4,})(?:\.\d{2})?)")
_RE_SPEAKER_TURN = re.compile(
    r"^\s*(?:>\s*)?(?:\*\*|__)?(?P<label>[A-Z][A-Za-z'\-]{1,15}(?:\s[A-Z][A-Za-z'\-]{1,15})?)"
    r"(?:\*\*|__)?\s*:\s+\S")

#: Labels that look like a speaker turn and are not one. Document headers and prose
#: openers are the dominant shape in a planning corpus; without this list the
#: transcript check fires on nearly every structured markdown file.
_NOT_SPEAKERS = frozenset({
    "note", "notes", "subject", "from", "to", "cc", "date", "status", "owner",
    "example", "examples", "warning", "caution", "objective", "todo", "verdict",
    "context", "problem", "solution", "result", "results", "summary", "scope",
    "why", "what", "how", "when", "where", "who", "impact", "risk", "evidence",
    "fix", "cause", "usage", "input", "output", "before", "after", "reach",
    "acceptance", "authority", "assignee", "author", "mode", "model", "gate",
    "supersedes", "tip", "warn", "error", "info", "debug", "step", "phase",
    "decision", "rationale", "finding", "findings", "action", "next", "deliver",
    "boundary", "boundaries", "reason", "detail", "details", "default", "returns",
    "raises", "args", "params", "type", "value", "key", "path", "file", "line",
})


def _is_round_amount(raw: str) -> bool:
    """Round illustrative figures are what the gate asks authors to use (Category 3)."""
    digits = raw.replace(",", "").replace(" ", "")
    if "." in digits:
        whole, frac = digits.split(".", 1)
        if frac not in ("00", "0"):
            return False
        digits = whole
    try:
        value = int(digits)
    except ValueError:
        return True
    for step in (1_000_000, 100_000, 10_000, 1_000, 500, 100):
        if value >= step:
            return value % step == 0
    return True


def scan_line_tier3(line: str, *, roster: Roster, boundaries: Sequence[str]) -> list:
    """Tier-3 findings for one line.

    Every check here is gated on the destination. At B2 with no stricter boundary in
    play, the participant-name check does not run at all — that is the constraint the
    whole tier lives or dies by.
    """
    out = []
    strict = [b for b in boundaries if b in CLASS_P_STRICT]

    roster_hit = False
    if strict and roster.configured:
        for name in roster.names:
            if re.search(r"\b" + re.escape(name) + r"\b", line):
                roster_hit = True
                out.append(("PARTICIPANT_NAME",
                            f"participant name at {'/'.join(strict)} — Class P is removed here",
                            mask(name), TIER_WARN))

    money_hit = False
    for match in _RE_MONEY.finditer(line):
        if not _is_round_amount(match.group(1)):
            money_hit = True
            out.append(("EXACT_AMOUNT",
                        "non-round monetary figure — use round illustrative figures (Category 3)",
                        mask(match.group(0)), TIER_WARN))

    # Dates fire only when corroborated by another tier-3 signal on the same line.
    # A bare date is the single noisiest pattern in this whole set: planning documents
    # are made of dates, and an uncorroborated date warning is how the tier gets
    # switched off. Corroboration is the price of keeping it at all.
    if strict and (roster_hit or money_hit):
        if re.search(r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b", line) or re.search(
                r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(?:19|20)\d{2}\b",
                line):
            out.append(("SPECIFIC_DATE",
                        "real-world date alongside a participant name or exact amount",
                        "", TIER_WARN))

    return out


def scan_transcript_shape(lines: Sequence[str], *, boundaries: Sequence[str],
                          threshold: int = 4) -> list:
    """Category 4 — prompt logs and transcripts are not extractable as-is.

    Detected by shape: a run of consecutive speaker-turn lines. Returns
    ``(line_no, code, message)`` tuples.

    A run only counts as a transcript when at least one speaker **recurs**. That single
    condition is what separates a dialogue from a definition list or an email header
    block, where every label is distinct and appears once — and those are the dominant
    shape in a planning corpus, so without it this check fires everywhere and gets
    turned off.
    """
    if not boundaries:
        return []
    out = []
    run_start = None
    run_labels: list = []

    def _flush() -> None:
        if len(run_labels) >= threshold and len(run_labels) > len(set(run_labels)):
            out.append((run_start, "TRANSCRIPT_SHAPE",
                        f"{len(run_labels)} speaker-turn lines with a recurring speaker — "
                        "extract findings, not transcripts"))

    for index, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        match = None
        if not stripped.startswith(("- ", "* ", "#", "|", "+")):
            match = _RE_SPEAKER_TURN.match(line)
        label = match.group("label").strip().lower() if match else None
        if label and label not in _NOT_SPEAKERS:
            if not run_labels:
                run_start = index
            run_labels.append(label)
        else:
            _flush()
            run_labels = []
            run_start = None
    _flush()
    return out


# --------------------------------------------------------------------------- #
# Tier 2 — the declaration
# --------------------------------------------------------------------------- #

_RE_DECL_HEADER = re.compile(r"\*\*Extraction declaration\*\*", re.IGNORECASE)
_RE_BOUNDARY_TOKEN = re.compile(r"\bB[1-4]\b")
_RE_NONE = re.compile(r"\bnone\b", re.IGNORECASE)

_DECL_FIELDS = (
    ("boundaries", re.compile(r"^\s*[-*]\s*Boundaries crossed\s*:\s*(.+)$", re.IGNORECASE)),
    ("class_a", re.compile(r"^\s*[-*]\s*Class A\b[^:]*:\s*(.+)$", re.IGNORECASE)),
    ("class_p", re.compile(r"^\s*[-*]\s*Class P\b[^:]*:\s*(.+)$", re.IGNORECASE)),
    ("judgement", re.compile(r"^\s*[-*]\s*Categories\s*3\s*[–\-]\s*5[^:]*:\s*(.+)$", re.IGNORECASE)),
    ("declared_by", re.compile(r"^\s*[-*]\s*Declared by\s*:\s*(.+)$", re.IGNORECASE)),
)

_RE_SIGNATURE = re.compile(r"^(?P<agent>[^·]+)·\s*session\s+(?P<sid>\S+)\s*·\s*(?P<date>.+)$",
                           re.IGNORECASE)


@dataclass
class Declaration:
    present: bool = False
    line: int = 0
    gate_ref: bool = False
    boundaries: tuple = ()
    fields: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


def parse_declaration(text: str) -> Declaration:
    """Parse the gate §7 block. The shape is Athena's and is not redefined here."""
    lines = text.splitlines()
    header_index = None
    for index, line in enumerate(lines):
        if _RE_DECL_HEADER.search(line):
            header_index = index
            break
    if header_index is None:
        return Declaration(present=False)

    decl = Declaration(present=True, line=header_index + 1)
    decl.gate_ref = GATE_DOC in lines[header_index] or any(
        GATE_DOC in ln for ln in lines[header_index:header_index + 3])
    if not decl.gate_ref:
        decl.errors.append("declaration does not reference the gate document")

    # The block runs to the first blank-line-terminated end of the bullet list.
    body = []
    for line in lines[header_index + 1:]:
        if line.strip() == "" and body:
            break
        body.append(line)

    for name, rx in _DECL_FIELDS:
        value = None
        for line in body:
            match = rx.match(line)
            if match:
                value = match.group(1).strip()
                break
        if value is None:
            decl.errors.append(f"declaration is missing the '{name}' line")
        else:
            decl.fields[name] = value

    raw_boundaries = decl.fields.get("boundaries", "")
    tokens = tuple(sorted(set(_RE_BOUNDARY_TOKEN.findall(raw_boundaries.upper()))))
    decl.boundaries = tokens
    if "boundaries" in decl.fields and not tokens and not _RE_NONE.search(raw_boundaries):
        decl.errors.append("declaration names no boundary (expected B1–B4, or 'none')")

    signature = decl.fields.get("declared_by", "")
    if signature and not _RE_SIGNATURE.match(signature):
        decl.errors.append("'Declared by' must read '<agent> · session <sid> · <date>'")

    return decl


# --------------------------------------------------------------------------- #
# Brief-side clause (PROJ-039/T-123)
# --------------------------------------------------------------------------- #

# Tolerates both markdown habits: `**Extraction-gate:**` and `**Extraction-gate**:`.
_RE_CLAUSE_GATE = re.compile(r"\*\*Extraction-gate\s*:?\s*\*\*\s*:?", re.IGNORECASE)
# The bullet marker is optional: a compliant brief can carry the line as bare prose
# straight under the `**Extraction-gate:**` heading, not just as a `- ` list item (the
# T-129 rev-2 brief did exactly this and the strict-bullet regex false-negatived on
# its own compliant line — banked as the nit that motivated this fix).
_RE_CLAUSE_AUTH = re.compile(r"^\s*(?:[-*]\s*)?Boundaries authorised\s*:\s*(.+)$", re.IGNORECASE)


@dataclass
class BriefAuthorisation:
    present: bool = False
    boundaries: tuple = ()
    explicit_none: bool = False
    errors: list = field(default_factory=list)

    def permits(self, boundary: str) -> bool:
        return boundary in self.boundaries


def parse_brief_authorisation(text: str) -> BriefAuthorisation:
    """Parse the T-123 clause out of a brief body."""
    auth = BriefAuthorisation()
    if not _RE_CLAUSE_GATE.search(text):
        auth.errors.append("brief carries no '**Extraction-gate:**' clause")
    value = None
    for line in text.splitlines():
        match = _RE_CLAUSE_AUTH.match(line)
        if match:
            value = match.group(1).strip()
            break
    if value is None:
        auth.errors.append("brief carries no 'Boundaries authorised:' line")
        return auth
    auth.present = True
    auth.boundaries = tuple(sorted(set(_RE_BOUNDARY_TOKEN.findall(value.upper()))))
    auth.explicit_none = bool(_RE_NONE.search(value)) and not auth.boundaries
    if not auth.boundaries and not auth.explicit_none:
        auth.errors.append(
            "'Boundaries authorised:' must read 'none' or name boundaries from B1–B4")
    return auth


# --------------------------------------------------------------------------- #
# File scanning
# --------------------------------------------------------------------------- #

def scan_text(path: str, text: str, *, config: DestinationConfig, roster: Roster,
              added_lines: Optional[Sequence[tuple]] = None,
              brief: Optional[BriefAuthorisation] = None) -> list:
    """Scan one artefact. ``added_lines`` is ``[(line_no, text), ...]``.

    When ``added_lines`` is None the whole file is treated as added — correct for a
    newly created file and for the substrate sweep, where there is no diff to take.
    """
    boundaries = config.boundaries_for(path)
    findings: list = []

    # Outside an extraction destination the gate does not fire at all. This is an
    # extraction control, not a general-purpose secret scanner: a credential in
    # application code is a real problem and a different control's problem, and
    # claiming it here is how this one ends up owning every false positive in the
    # repository. It also makes the `exempt` list total, which is what an exemption
    # should mean.
    if not boundaries:
        return findings

    if added_lines is None:
        added_lines = list(enumerate(text.splitlines(), start=1))

    # --- tiers 1 and 3: the extract, i.e. added lines only -----------------
    for line_no, line in added_lines:
        for code, message, excerpt, tier in scan_line_tier1(
                line, roster=roster, boundaries=boundaries):
            findings.append(Finding(tier=tier, code=code, path=path, line=line_no,
                                    message=message, excerpt=excerpt,
                                    boundary=boundaries[0] if boundaries else None))
        for code, message, excerpt, tier in scan_line_tier3(
                line, roster=roster, boundaries=boundaries):
            findings.append(Finding(tier=tier, code=code, path=path, line=line_no,
                                    message=message, excerpt=excerpt,
                                    boundary=boundaries[0] if boundaries else None))

    for line_no, code, message in scan_transcript_shape(
            [ln for _, ln in added_lines], boundaries=boundaries):
        anchor = added_lines[line_no - 1][0] if 0 < line_no <= len(added_lines) else 0
        findings.append(Finding(tier=TIER_WARN, code=code, path=path, line=anchor,
                                message=message))

    # --- tier 2: the declaration, over the whole artefact -------------------
    if boundaries and config.needs_declaration(path):
        findings.extend(_declaration_findings(path, text, boundaries, brief))

    return findings


def _declaration_findings(path: str, text: str, boundaries: Sequence[str],
                          brief: Optional[BriefAuthorisation]) -> list:
    out = []
    decl = parse_declaration(text)
    reach = "/".join(f"{b} ({BOUNDARY_LABELS[b]})" for b in boundaries)

    if not decl.present:
        out.append(Finding(
            tier=TIER_STRUCTURAL, code="DECLARATION_MISSING", path=path, line=0,
            message=(f"extraction destination {reach} — no §7 declaration block. "
                     f"See {GATE_DOC} §7."),
            boundary=boundaries[0]))
        return out

    for error in decl.errors:
        out.append(Finding(tier=TIER_STRUCTURAL, code="DECLARATION_MALFORMED", path=path,
                           line=decl.line, message=error, boundary=boundaries[0]))

    missing = [b for b in boundaries if b not in decl.boundaries]
    if missing and decl.boundaries:
        out.append(Finding(
            tier=TIER_STRUCTURAL, code="DECLARATION_BOUNDARY_MISMATCH", path=path,
            line=decl.line,
            message=(f"path is an extraction destination for {'/'.join(missing)} but the "
                     f"declaration names {'/'.join(decl.boundaries)}"),
            boundary=missing[0]))

    if brief is not None and brief.present:
        widened = [b for b in decl.boundaries if not brief.permits(b)]
        if widened:
            authorised = "/".join(brief.boundaries) if brief.boundaries else "none"
            out.append(Finding(
                tier=TIER_STRUCTURAL, code="BOUNDARY_WIDENED", path=path, line=decl.line,
                message=(f"declaration crosses {'/'.join(widened)} but the brief authorises "
                         f"{authorised} — an agent does not widen its own boundary (gate §3.1)"),
                boundary=widened[0]))

    return out


def is_text_file(path: str) -> bool:
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def partition(findings: Iterable[Finding]) -> dict:
    buckets = {TIER_HARD: [], TIER_STRUCTURAL: [], TIER_WARN: []}
    for finding in findings:
        buckets[finding.tier].append(finding)
    return buckets
