"""Noisy-mic behaviour of the silence gate and calibration (no GPU / Whisper needed)."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatscribe.audio import TARGET_SR, prepare  # noqa: E402
from chatscribe.calibration import compute_levels  # noqa: E402

RNG = np.random.default_rng(7)


def _noise(seconds, db):
    return (RNG.normal(0, 1, int(TARGET_SR * seconds)) * 10 ** (db / 20)).astype(np.float32)


def _speechish(seconds, db):
    """Amplitude-modulated noise burst - loud/soft like syllables."""
    t = np.arange(int(TARGET_SR * seconds)) / TARGET_SR
    env = 0.55 + 0.45 * np.sin(2 * np.pi * 4 * t)
    return (_noise(seconds, db) * env).astype(np.float32)


def noisy_utterance(noise_db=-40, speech_db=-33):
    x = _noise(2.0, noise_db)
    s = int(TARGET_SR * 0.6)
    x[s:s + int(TARGET_SR * 0.9)] += _speechish(0.9, speech_db)
    return x


def test_gate_calibrated_too_high_still_keeps_speech():
    # The reported bug: noisy mic, gate far above the voice -> "Didn't hear anything".
    out, info = prepare(noisy_utterance(), gain=1.0, gate_db=-20.0)
    assert not info["too_quiet"], info
    assert info["gate_used_db"] < -20.0
    assert len(out) > TARGET_SR * 0.8


def test_real_silence_is_still_silence():
    _, info = prepare(_noise(1.5, -80), gain=1.0, gate_db=-50.0)
    assert info["too_quiet"]


def test_clean_mic_trims_to_speech():
    x = noisy_utterance(noise_db=-75, speech_db=-25)
    out, info = prepare(x, gain=1.0, gate_db=-50.0)
    assert not info["too_quiet"]
    assert len(out) < len(x)          # silence around the speech was trimmed


def test_recalibrating_does_not_stack_gain():
    noise = list(RNG.normal(-40, 2, 150))
    speech = list(RNG.normal(-12, 4, 300))          # loud mic -> gain below 1
    first = compute_levels(noise, speech, current_gain=1.0)
    again = compute_levels(noise, speech, current_gain=first.gain)
    assert abs(first.gain - again.gain) < 1e-6
    assert abs(first.gate_db - again.gate_db) < 1e-6


def test_noisy_mic_gate_stays_below_voice():
    noise = list(RNG.normal(-38, 3, 150)) + [-10.0] * 8   # hiss + a cough during "silence"
    speech = list(RNG.normal(-33, 4, 300))
    r = compute_levels(noise, speech)
    assert r.gate_db <= r.speech_db - 12
