"""Stop-word vocabulary loader (Change B + C, Iter-2 Strand 1).

Vendored scikit-learn ENGLISH_STOP_WORDS (318 words; BSD-3-Clause; Glasgow IR
stop list per F-2-002), extracted to a flat file at `data/stop-words-en.txt`
so the detector carries no runtime scikit-learn dependency.

Used by:
  - Cat 6 (terminology drift) — skip stop-word canonical + observed tokens.
  - Cat 3 (out-of-context over-emphasis) — skip terms that are entirely
    stop words.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

STOP_WORDS_PATH = Path(__file__).resolve().parent / "data" / "stop-words-en.txt"

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


@lru_cache(maxsize=8)
def load_stop_words(path: str | None = None) -> frozenset[str]:
    """Load the vendored stop-word vocabulary as a lowercase frozenset.

    Blank lines and `#` comment lines are ignored. Result is cached so repeated
    detector runs share one frozenset (determinism + speed).
    """
    p = Path(path) if path else STOP_WORDS_PATH
    words: set[str] = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            words.add(line.lower())
    return frozenset(words)


def is_stop_word(token: str, stop_words: frozenset[str]) -> bool:
    """True if a single token is a stop word (case-insensitive)."""
    return token.lower() in stop_words


def phrase_is_all_stop(phrase: str, stop_words: frozenset[str]) -> bool:
    """True if every alphanumeric word in a phrase is a stop word.

    Used by Cat 3: a capitalised phrase / backtick token composed entirely of
    stop words carries no conceptual weight and should not be flagged as
    over-emphasis. A phrase with no extractable words is treated as all-stop
    (nothing to flag). With an empty vocabulary this always returns False so
    the guard is a no-op when stop-words are not loaded.
    """
    if not stop_words:
        return False
    words = _WORD_RE.findall(phrase.lower())
    if not words:
        return True
    return all(w in stop_words for w in words)
