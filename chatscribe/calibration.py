"""Calibration: mic noise floor, speaking level, and an accuracy test that LEARNS
how this particular player's voice gets misheard on WoW words."""

from __future__ import annotations

import difflib
import re
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from .lexicon import Lexicon, zipf
from .phonetics import norm

LEVEL_SENTENCE = ("Looking for more for Upper Blackrock Spire, we need one healer "
                  "and then we head to Orgrimmar.")

TEST_PHRASES = [
    "Looking for more for Upper Blackrock Spire, need one healer.",
    "Meet me in Orgrimmar, then we fly to Gadgetzan.",
    "Who is tanking Ragnaros in Molten Core tonight?",
    "Selling Black Lotus and Arcanite Bars, whisper me.",
    "We wiped on Vaelastrasz again, everyone run back.",
    "Is anyone farming Stratholme or Scholomance?",
    "Ahn'Qiraj opens soon and C'Thun is the last boss.",
    "Summon to Zul'Gurub please, I have the Hakkar quest.",
    "Bindings of the Windseeker dropped from Baron Geddon.",
    "Drakkisath and Rivendare are on farm, Naxxramas is next.",
    "Heading to Un'Goro Crater and Silithus after Tanaris.",
    "Kel'Thuzad and Sapphiron are the hardest bosses in Naxxramas.",
]


@dataclass
class LevelResult:
    noise_db: float = -65.0
    speech_db: float = -25.0
    gate_db: float = -50.0
    gain: float = 1.0
    verdict: str = ""


def measure_levels(recorder, seconds: float, on_tick=None) -> list[float]:
    """Collect 20 ms frame levels from the live mic for `seconds`."""
    levels: list[float] = []
    done = threading.Event()
    t_end = time.monotonic() + seconds

    def listener(lvl, _block):
        levels.append(lvl)
        if time.monotonic() >= t_end:
            done.set()

    recorder.level_listeners.append(listener)
    try:
        while not done.wait(0.1):
            if on_tick:
                on_tick(max(0.0, t_end - time.monotonic()))
    finally:
        recorder.level_listeners.remove(listener)
    return levels


def compute_levels(noise_levels: list[float], speech_levels: list[float],
                   current_gain: float = 1.0) -> LevelResult:
    """Levels come from the recorder BEFORE gain, so the new gain is absolute -
    it never stacks on a previous calibration (that used to make re-calibrating
    a loud/noisy mic push the gate far above the voice)."""
    r = LevelResult()
    n = np.array(noise_levels) if noise_levels else np.array([-70.0])
    s = np.array(speech_levels) if speech_levels else np.array([-30.0])
    # Median-ish noise: a cough or keyboard click during the silence step must not count.
    noise = float(np.percentile(n, 60))
    voiced = s[s > noise + 6]
    if voiced.size > 15:
        speech = float(np.percentile(voiced, 75))
    else:
        speech = float(np.percentile(s, 90))   # barely above the noise: best effort
    # Aim the speaking level at about -24 dBFS after gain; never more than +18 dB.
    want_gain_db = float(np.clip(-24 - speech, -12, 18))
    r.gain = round(10 ** (want_gain_db / 20), 2)
    r.noise_db = round(noise + want_gain_db, 1)
    r.speech_db = round(speech + want_gain_db, 1)
    gap = r.speech_db - r.noise_db
    gate = r.noise_db + max(4.0, 0.35 * gap)
    # The gate must stay well under the voice, whatever the noise did.
    gate = min(gate, r.speech_db - 12.0, -30.0)
    r.gate_db = round(max(gate, -72.0), 1)
    if gap < 10:
        r.verdict = ("Noisy mic: your voice is barely louder than the background. It will still "
                     "work (ChatScribe's speech detector handles noise), but moving the mic closer "
                     "or turning on your mic's noise suppression will improve accuracy.")
    elif gap < 20:
        r.verdict = "OK, but noisy. Recognition should work; a closer mic will help."
    else:
        r.verdict = "Great signal - clean and clear."
    return r


@dataclass
class PhraseResult:
    expected: str
    raw: str
    final: str
    raw_acc: float
    final_acc: float
    learned: list = field(default_factory=list)   # (heard, canonical)


def _words(s: str) -> list[str]:
    return norm(re.sub(r"[^\w\s']", " ", s)).split()


def word_accuracy(expected: str, got: str) -> float:
    e, g = _words(expected), _words(got)
    if not e:
        return 1.0
    sm = difflib.SequenceMatcher(a=e, b=g, autojunk=False)
    return sum(b.size for b in sm.get_matching_blocks()) / len(e)


def learn_from(expected: str, raw: str, final: str, lexicon: Lexicon) -> list[tuple[str, str]]:
    """Find WoW words that came out wrong and teach the lexicon the misheard form."""
    exp_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9']*", expected)
    raw_tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9']*", raw)
    e = [norm(t) for t in exp_tokens]
    r = [norm(t) for t in raw_tokens]
    final_norm = " ".join(_words(final))
    learned = []
    sm = difflib.SequenceMatcher(a=e, b=r, autojunk=False)
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op != "replace" or (a1 - a0) > 4 or (b1 - b0) > 5 or b1 == b0:
            continue
        exp_chunk = exp_tokens[a0:a1]
        # Only learn WoW words (rare in English or present in the lexicon).
        if not any(w.lower() in lexicon.canonical_words or zipf(w) < 3.0 for w in exp_chunk):
            continue
        canonical = " ".join(exp_chunk)
        if " ".join(norm(w) for w in exp_chunk) in final_norm:
            continue    # the corrector already fixed it
        heard = " ".join(raw_tokens[b0:b1])
        if lexicon.learn(heard, canonical):
            learned.append((heard, canonical))
    return learned
