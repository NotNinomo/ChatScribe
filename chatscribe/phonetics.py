"""A small phonetic key tuned for fantasy names.

Goal: two spellings that SOUND alike get the same (or a very close) key, e.g.
    "Ahn'Qiraj" / "on kiraj"   "Orgrimmar" / "or grimmer"   "C'Thun" / "see thun"
Vowels collapse to a single placeholder so syllable rhythm still counts, but
"o" vs "a" vs "u" stops mattering - that's where speech recognisers usually slip.
"""

from __future__ import annotations

import re
from functools import lru_cache

_RULES = [
    (r"[^a-z0-9]", ""),
    (r"([b-df-hj-np-tv-z])\1+", r"\1"),   # double consonants first: "pph" -> "ph", "xx" -> "x"
    (r"^ps", "s"), (r"^kn", "n"), (r"^gn", "n"), (r"^wr", "r"), (r"^x", "s"),
    (r"tch", "ch"), (r"sch", "sk"), (r"ph", "f"), (r"gh", "g"), (r"ck", "k"),
    (r"qu", "kw"), (r"q", "k"), (r"x", "k"), (r"dg", "j"), (r"wh", "w"),
    (r"kh", "k"), (r"zh", "j"), (r"th", "t"), (r"ch(?=[rl])", "k"),
    (r"c(?=[eiy])", "s"), (r"c", "k"),
    (r"z", "s"), (r"v", "f"), (r"sh", "S"), (r"ch", "C"),
    (r"(?<=[a-z])h", ""), (r"^h", ""),
    (r"(?<=[aeiou])w(?=[^aeiouy]|$)", ""),   # "hawker" ~ "hakkar"
    (r"y(?=[^aeiou]|$)", "i"),
    (r"(?<=[^aeiou])e$", ""),          # silent trailing e: "gore" ~ "gor"
    (r"[aeiouy]+", "a"),
    (r"(.)\1+", r"\1"),
]
_COMPILED = [(re.compile(p), r) for p, r in _RULES]


@lru_cache(maxsize=50000)
def key(text: str) -> str:
    s = text.lower()
    for rx, rep in _COMPILED:
        s = rx.sub(rep, s)
    return s


def norm(text: str) -> str:
    """Normalisation used for exact matching: lowercase, no apostrophes/dots, single spaces."""
    s = text.lower().replace("\u2019", "'").replace("\u2018", "'")
    s = re.sub(r"[\'\.]", "", s)
    s = re.sub(r"[-_/:,!?;\"()]", " ", s)
    return " ".join(s.split())
