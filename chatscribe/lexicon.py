"""WoW vocabulary: loading, exact alias replacement and sound-alike correction."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process
from wordfreq import zipf_frequency

from . import DATA_DIR, LEXICON_DIR
from .phonetics import key as pkey
from .phonetics import norm

log = logging.getLogger(__name__)
LEARNED_PATH = DATA_DIR / "learned_aliases.json"

# A word this common in English is never "corrected" into a WoW name by sound.
COMMON_ZIPF = 3.6
# A single-word canonical at least this common keeps the user's own casing
# ("rogue", "horde", "fear"); rarer ones are real names ("Orgrimmar", "Hakkar").
NAME_ZIPF = 2.5

WORD_RX = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\u2019]*(?:-[A-Za-z0-9]+)*")


@dataclass
class Entry:
    canonical: str
    fuzzy: bool = True
    aliases: list = field(default_factory=list)
    source: str = ""


@dataclass
class Token:
    text: str
    start: int
    end: int


@dataclass
class Replacement:
    start: int
    end: int
    original: str
    new: str
    kind: str        # exact | alias | sound
    score: float = 100.0


def zipf(word: str) -> float:
    return zipf_frequency(word.lower(), "en")


def _spelled_forms(abbr: str) -> list[str]:
    """LFG -> ["l f g", "l.f.g", "l-f-g"] ; AQ40 -> ["a q 40"]"""
    m = re.fullmatch(r"([A-Z]{2,6})(\d*)", abbr)
    if not m:
        return []
    letters = " ".join(m.group(1).lower())
    tail = f" {m.group(2)}" if m.group(2) else ""
    return [letters + tail]


class Lexicon:
    def __init__(self, folder: Path = LEXICON_DIR, shorthand: bool = False):
        self.folder = folder
        self.shorthand = shorthand
        self.entries: list[Entry] = []
        self.exact: dict[str, tuple[str, bool, str]] = {}  # norm alias -> (canonical, shorthand_only, kind)
        self.max_words = 1
        self.fuzzy_keys: list[str] = []
        self.fuzzy_canon: list[str] = []
        self.canonical_words: set[str] = set()
        self.learned: dict[str, str] = {}
        self.reload()

    # ----------------------------------------------------------- loading
    def reload(self) -> None:
        self.entries.clear()
        self.exact.clear()
        custom, example = self.folder / "custom.txt", self.folder / "custom.example.txt"
        if not custom.exists() and example.exists():
            try:
                custom.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
            except OSError:
                pass        # read-only install: fine, there's just no custom list yet
        for path in sorted(self.folder.glob("*.txt")):
            if path.name.lower() in ("readme.txt", "custom.example.txt"):
                continue
            self._load_file(path)
        self.learned = {}
        if LEARNED_PATH.exists():
            try:
                self.learned = json.loads(LEARNED_PATH.read_text(encoding="utf-8"))
            except Exception as e:
                log.error("learned_aliases.json unreadable: %s", e)
        for alias, canon in self.learned.items():
            self._add_exact(alias, canon, False, "alias", override=True)
        self._build_fuzzy_index()
        log.info("Lexicon: %d entries, %d exact forms, %d sound-matchable",
                 len(self.entries), len(self.exact), len(self.fuzzy_keys))

    def _load_file(self, path: Path) -> None:
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or (line.startswith("[") and line.endswith("]")):
                continue
            fuzzy = True
            if line.startswith("!"):
                fuzzy = False
                line = line[1:].strip()
            canon, _, rest = line.partition("=")
            canon = canon.strip()
            if not canon:
                continue
            aliases = [a.strip() for a in rest.split(";") if a.strip()] if rest else []
            e = Entry(canon, fuzzy, aliases, path.name)
            self.entries.append(e)
            self._add_exact(canon, canon, False, "exact")
            for f in _spelled_forms(canon):
                self._add_exact(f, canon, False, "alias")
            for a in aliases:
                sh = a.startswith(">")
                a = a.lstrip(">").strip()
                self._add_exact(a, _target_for_alias(a, canon), sh, "alias")
            for w in WORD_RX.findall(canon):
                self.canonical_words.add(w.lower())

    def _add_exact(self, form: str, canon: str, shorthand_only: bool, kind: str,
                   override: bool = False) -> None:
        k = norm(form)
        if not k:
            return
        if k in self.exact and not override:
            # Keep the first definition, but a real canonical beats an alias.
            if not (kind == "exact" and self.exact[k][2] != "exact"):
                return
        self.exact[k] = (canon, shorthand_only, kind)
        self.max_words = max(self.max_words, len(k.split()))

    def _build_fuzzy_index(self) -> None:
        seen = set()
        self.fuzzy_keys, self.fuzzy_canon = [], []
        for e in self.entries:
            if not e.fuzzy:
                continue
            n = norm(e.canonical)
            # Only names that aren't plain English are worth sound-matching.
            words = n.split()
            if all(zipf(w) >= COMMON_ZIPF for w in words):
                continue
            if len(n.replace(" ", "")) < 4:
                continue
            k = pkey(n)
            if (k, e.canonical) in seen:
                continue
            seen.add((k, e.canonical))
            self.fuzzy_keys.append(k)
            self.fuzzy_canon.append(e.canonical)

    # -------------------------------------------------------- learning
    def learn(self, heard: str, canonical: str) -> bool:
        k = norm(heard)
        if not k or k == norm(canonical):
            return False
        if all(zipf(w) >= 5.0 for w in k.split()):
            return False  # never learn "the", "and you" etc.
        self.learned[k] = canonical
        LEARNED_PATH.write_text(json.dumps(self.learned, indent=2, ensure_ascii=False),
                                encoding="utf-8")
        self._add_exact(k, canonical, False, "alias", override=True)
        return True

    # --------------------------------------------------------- prompts
    def prompt_terms(self, limit_chars: int = 420) -> list[str]:
        """Terms to feed Whisper's prompt: user's own words first, then the hard core set."""
        out: list[str] = []
        custom = [e.canonical for e in self.entries if e.source == "custom.txt"]
        learned = list(dict.fromkeys(self.learned.values()))
        pool = custom + learned + CORE_PROMPT_TERMS
        total = 0
        for t in dict.fromkeys(pool):
            if total + len(t) + 2 > limit_chars:
                break
            out.append(t)
            total += len(t) + 2
        return out

    # ------------------------------------------------------ correcting
    def correct(self, text: str, enable_fuzzy: bool = True) -> tuple[str, list[Replacement]]:
        text = _collapse_dotted_acronyms(text)
        text = self._split_glued_acronyms(text)
        tokens = [Token(m.group(0), m.start(), m.end()) for m in WORD_RX.finditer(text)]
        reps: list[Replacement] = []
        i = 0
        n_tok = len(tokens)
        while i < n_tok:
            hit = None
            # Longest window first; at each size try exact/alias, then sound-alike.
            for n in range(min(self.max_words, n_tok - i), 0, -1):
                exact_ok = _joinable(text, tokens, i, n, allow_comma=True)
                if not exact_ok:
                    continue
                span = tokens[i:i + n]
                original = text[span[0].start:span[-1].end]
                hit = self._exact_hit(original, span)
                if not hit and enable_fuzzy and n <= 4 and _joinable(text, tokens, i, n):
                    hit = self._fuzzy_hit(original, span)
                if hit:
                    break
            if hit:
                rep, n = hit
                reps.append(rep)        # unchanged hits too: they protect the name's casing
                i += n
            else:
                i += 1
        out = text
        for r in sorted(reps, key=lambda r: r.start, reverse=True):
            out = out[:r.start] + r.new + out[r.end:]
        return out, reps

    @staticmethod
    def changed(reps: list[Replacement]) -> list[Replacement]:
        return [r for r in reps if r.new != r.original]

    def _split_glued_acronyms(self, text: str) -> str:
        """Whisper sometimes glues spelled letters together: "LFGUBRS" -> "LFG UBRS"."""
        def split(word: str):
            w = word.lower()
            best = [None] * (len(w) + 1)
            best[0] = []
            for i in range(len(w)):
                if best[i] is None:
                    continue
                for j in range(i + 2, min(len(w), i + 7) + 1):
                    hit = self.exact.get(w[i:j])
                    if hit and hit[0].replace(" ", "").isupper() and not hit[1]:
                        cand = best[i] + [hit[0]]
                        if best[j] is None or len(cand) < len(best[j]):
                            best[j] = cand
            parts = best[len(w)]
            return " ".join(parts) if parts and len(parts) > 1 else word

        def rep(m):
            word = m.group(0)
            if norm(word) in self.exact:
                return word
            return split(word)
        return re.sub(r"\b[A-Z0-9]{5,14}\b", rep, text)

    def _exact_hit(self, original: str, span: list[Token]):
        k = norm(original)
        suffix = ""
        found = self.exact.get(k)
        if not found and k.endswith("s") and len(k) >= 5:
            # Onyxia's -> Onyxia + 's ;  Arcanite Bars -> Arcanite Bar + s
            base = self.exact.get(k[:-1])
            if base and not _is_abbrev(base[0]):
                found = base
                suffix = "'s" if ("'" in original or "’" in original) else "s"
        if not found:
            return None
        canon, sh_only, kind = found
        if sh_only and not self.shorthand:
            return None
        new = canon + suffix
        single = len(canon.split()) == 1
        if kind == "exact":
            if canon.islower():
                return None   # slang written lowercase: nothing to fix
            if single and not _is_abbrev(canon) and _name_zipf(canon) >= NAME_ZIPF:
                return None   # "rogue", "horde", "fear": keep the user's own casing
        else:
            # Alias to an ordinary word: follow the user's casing ("rouge" -> "rogue").
            if single and not _is_abbrev(canon) and _name_zipf(canon) >= NAME_ZIPF \
                    and original[:1].islower():
                new = new[:1].lower() + new[1:]
        new = _match_case_context(original, new)
        return Replacement(span[0].start, span[-1].end, original, new,
                           "exact" if kind == "exact" else "alias"), len(span)

    def _fuzzy_hit(self, original: str, span: list[Token]):
        if not self.fuzzy_keys:
            return None
        words = [norm(t.text) for t in span]
        if not any(w and zipf(w) < COMMON_ZIPF for w in words):
            return None                    # all normal English: leave it alone
        if any(w in self.canonical_words for w in words) and len(span) == 1:
            return None
        common = [w for w in words if w and zipf(w) >= 5.3]
        joined = "".join(words)
        if len(joined) < 4:
            return None
        qk = pkey(joined)
        cands = process.extract(qk, self.fuzzy_keys, scorer=fuzz.ratio, limit=8,
                                score_cutoff=66)
        best = None
        for _ck, ks, idx in cands:
            canon = self.fuzzy_canon[idx]
            cwords = set(norm(canon).split())
            # Never swallow "the", "and", "is"... unless the name itself contains that word.
            if any(w not in cwords for w in common):
                continue
            cnorm = norm(canon).replace(" ", "")
            if abs(len(cnorm) - len(joined)) > max(2, int(0.4 * len(cnorm))):
                continue
            ts = fuzz.ratio(joined, cnorm)
            score = 0.65 * ks + 0.35 * ts
            L = len(cnorm)
            need = 90 if L <= 5 else 84 if L <= 7 else 80 if L <= 9 else 77
            if len(span) > 1:
                need += 2
            if ks >= 99 and L >= 5 and ts >= 45:
                need = min(need, 80)       # sounds identical: "hawker" ~ "Hakkar"
            if score >= need and (best is None or score > best[1]):
                best = (canon, score)
        if not best:
            return None
        return Replacement(span[0].start, span[-1].end, original, best[0], "sound",
                           best[1]), len(span)


def _name_zipf(canon: str) -> float:
    """How common a one-word canonical is in English ("Un'Goro" -> ~0, "Rogue" -> 3.5)."""
    return zipf(norm(canon).replace(" ", ""))


def _is_abbrev(canon: str) -> bool:
    """LFG, BoK, PW:S, AQ40 - but not "Arcanite Bar"."""
    return any(sum(c.isupper() for c in w) >= 2 for w in re.split(r"[\s:/]+", canon))


def _joinable(text: str, tokens: list[Token], i: int, n: int, allow_comma: bool = False) -> bool:
    """Words i..i+n may be merged only if separated by plain spaces (or a hyphen)."""
    ok = " -," if allow_comma else " -"
    for j in range(i, i + n - 1):
        gap = text[tokens[j].end:tokens[j + 1].start]
        if gap.strip(ok) != "" or gap.count(",") > 1:
            return False
    return True


_DOTTED = re.compile(r"\b((?:[A-Za-z]\.){2,6})(?=\s|$|[,!?])")


def _collapse_dotted_acronyms(text: str) -> str:
    """"L.F.G." -> "LFG" (Whisper sometimes writes spelled letters this way)."""
    def rep(m):
        letters = m.group(1).replace(".", "")
        if letters.lower() in ("eg", "ie", "am", "pm"):
            return m.group(1)
        return letters.upper()
    return _DOTTED.sub(rep, text)


def _match_case_context(original: str, new: str) -> str:
    # "the barrens" -> "the Barrens", not "The Barrens" mid-sentence.
    if new.startswith("The ") and original[:4].lower() == "the " and original[0] == "t":
        return "the " + new[4:]
    return new


def _target_for_alias(alias: str, canon: str) -> str:
    """A short alias of a long title maps to the matching part of the title:
    "drakisath" under "General Drakkisath" -> "Drakkisath", not the full title."""
    a_words = norm(alias).split()
    c_words = canon.split()
    if len(a_words) >= len(c_words) or len(c_words) < 2:
        return canon
    a = "".join(a_words)
    best, best_score = canon, 0.0
    for size in range(1, len(c_words)):
        for start in range(0, len(c_words) - size + 1):
            part = " ".join(c_words[start:start + size]).rstrip(",")
            score = 0.6 * fuzz.ratio(pkey(a), pkey(norm(part))) + \
                0.4 * fuzz.ratio(a, norm(part).replace(" ", ""))
            if score > best_score:
                best, best_score = part, score
    return best if best_score >= 78 else canon


# Hard-to-spell names that benefit most from being in Whisper's prompt.
CORE_PROMPT_TERMS = [
    "Orgrimmar", "Ironforge", "Stormwind", "Darnassus", "Thunder Bluff", "Undercity",
    "Azeroth", "Kalimdor", "Ahn'Qiraj", "C'Thun", "Zul'Gurub", "Zul'Farrak", "Naxxramas",
    "Kel'Thuzad", "Ragnaros", "Onyxia", "Nefarian", "Molten Core", "Blackwing Lair",
    "Blackrock Depths", "Stratholme", "Scholomance", "Gnomeregan", "Uldaman", "Maraudon",
    "Dire Maul", "Deadmines", "Teldrassil", "Tirisfal", "Durotar", "Mulgore", "Silithus",
    "Un'Goro", "Tanaris", "Gadgetzan", "Feralas", "Azshara", "Winterspring", "Felwood",
    "Stranglethorn", "Booty Bay", "Hakkar", "Thunderfury", "Sulfuras", "Atiesh",
    "Vaelastrasz", "Chromaggus", "Huhuran", "Viscidus", "Sapphiron", "Loatheb",
    "Drakkisath", "Rivendare", "Quel'Thalas", "Sylvanas", "Thrall", "Vol'jin",
    "LFG", "LFM", "DPS", "WTS", "WTB", "UBRS", "LBRS", "BRD", "AQ40", "ZG", "MC", "BWL",
]
