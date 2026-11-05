"""Microphone capture with pre-roll, level metering, noise gate and normalisation."""

from __future__ import annotations

import collections
import logging
import threading
import time

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

TARGET_SR = 16000
BLOCK_MS = 20
ABS_SILENCE_DB = -58.0      # loudest part of an utterance below this (after gain) = silence
GATE_BELOW_SPEECH_DB = 14.0 # trimming gate is always at least this far below the speech


def db(x: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(np.square(x), dtype=np.float64))) if x.size else 0.0
    return 20 * np.log10(max(rms, 1e-9))


def list_input_devices() -> list[str]:
    out = []
    try:
        hostapis = sd.query_hostapis()
        for d in sd.query_devices():
            if d["max_input_channels"] > 0:
                api = hostapis[d["hostapi"]]["name"]
                if "WDM-KS" in api or "DirectSound" in api:
                    continue
                out.append(f"{d['name']} [{api}]")
    except Exception as e:
        log.error("Could not list audio devices: %s", e)
    return list(dict.fromkeys(out))


def _resolve_device(name: str):
    if not name:
        return None
    hostapis = sd.query_hostapis()
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and f"{d['name']} [{hostapis[d['hostapi']]['name']}]" == name:
            return i
    log.warning("Microphone %r not found, using default", name)
    return None


def resample(x: np.ndarray, sr: int, target: int = TARGET_SR) -> np.ndarray:
    if sr == target or x.size == 0:
        return x.astype(np.float32)
    # Windowed-sinc would be nicer; a short FIR low-pass + linear interp is plenty for speech.
    if sr > target:
        taps = 31
        cutoff = 0.45 * target / sr
        n = np.arange(taps) - (taps - 1) / 2
        h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
        h /= h.sum()
        x = np.convolve(x, h, mode="same")
    t_new = np.arange(0, len(x) * target / sr) * (sr / target)
    return np.interp(t_new, np.arange(len(x)), x).astype(np.float32)


class Recorder:
    """Keeps the mic stream open so the first syllable is never cut off (pre-roll).
    Audio is only kept while you hold push-to-talk; nothing is written to disk."""

    def __init__(self, device: str = "", preroll_ms: int = 300, tail_ms: int = 250,
                 max_seconds: int = 45):
        self.device_name = device
        self.preroll_ms = preroll_ms
        self.tail_ms = tail_ms
        self.max_seconds = max_seconds
        self.stream: sd.InputStream | None = None
        self.sr = TARGET_SR
        self._pre = collections.deque()
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._stop_at: float | None = None
        self._done = threading.Event()
        self._lock = threading.Lock()
        self.level_db = -100.0          # live level for meters
        self.level_listeners: list = []  # callables(level_db) - used by calibration

    # ------------------------------------------------------------ stream
    def open(self) -> None:
        self.close()
        dev = _resolve_device(self.device_name)
        for sr in (TARGET_SR, None):
            try:
                if sr is None:
                    sr = int(sd.query_devices(dev, "input")["default_samplerate"])
                blocksize = int(sr * BLOCK_MS / 1000)
                self.stream = sd.InputStream(device=dev, samplerate=sr, channels=1,
                                             dtype="float32", blocksize=blocksize,
                                             callback=self._callback, latency="low")
                self.stream.start()
                self.sr = sr
                log.info("Mic open: %s @ %d Hz", self.device_name or "default", sr)
                return
            except Exception as e:
                log.warning("Mic open at %s Hz failed: %s", sr, e)
                self.stream = None
        raise RuntimeError("Could not open the microphone. Check Windows privacy settings "
                           "(Settings > Privacy > Microphone) and the device in ChatScribe.")

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None

    def _callback(self, indata, frames, time_info, status):  # audio thread
        block = indata[:, 0].copy()
        lvl = db(block)
        self.level_db = lvl
        for fn in list(self.level_listeners):
            try:
                fn(lvl, block)
            except Exception:
                pass
        with self._lock:
            if self._recording:
                self._chunks.append(block)
                total = sum(len(c) for c in self._chunks) / self.sr
                if (self._stop_at and time.monotonic() >= self._stop_at) or total >= self.max_seconds:
                    self._recording = False
                    self._done.set()
            else:
                self._pre.append(block)
                max_blocks = max(1, int(self.preroll_ms / BLOCK_MS))
                while len(self._pre) > max_blocks:
                    self._pre.popleft()

    # --------------------------------------------------------- utterance
    @property
    def recording(self) -> bool:
        return self._recording

    def begin(self) -> None:
        with self._lock:
            self._chunks = list(self._pre)
            self._pre.clear()
            self._stop_at = None
            self._done.clear()
            self._recording = True

    def request_stop(self) -> None:
        """Non-blocking (safe to call from a keyboard hook): stop after the tail."""
        with self._lock:
            if self._recording and self._stop_at is None:
                self._stop_at = time.monotonic() + self.tail_ms / 1000

    def collect(self, timeout: float = 2.0) -> np.ndarray:
        """Wait for the tail to finish and return mono float32 audio at 16 kHz."""
        if self.stream is None or not self.stream.active:
            self._done.set()                    # mic gone: don't wait for callbacks that never come
        self._done.wait(timeout + self.tail_ms / 1000)
        with self._lock:
            self._recording = False
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
            self._stop_at = None
        return resample(audio, self.sr)

    def end(self, timeout: float = 2.0) -> np.ndarray:
        self.request_stop()
        return self.collect(timeout)

    def abort(self) -> None:
        with self._lock:
            self._recording = False
            self._chunks = []
            self._done.set()


def prepare(audio: np.ndarray, gain: float, gate_db: float) -> tuple[np.ndarray, dict]:
    """Gain, trim silence with the calibrated gate, normalise. Returns (audio, stats)."""
    info = {"peak_db": -100.0, "speech_ms": 0, "too_quiet": False}
    if audio.size == 0:
        info["too_quiet"] = True
        return audio, info
    x = audio * gain
    x = x - np.mean(x)                          # remove DC offset
    frame = int(TARGET_SR * BLOCK_MS / 1000)
    n = len(x) // frame
    if n == 0:
        info["too_quiet"] = True
        return x, info
    levels = np.array([db(x[i * frame:(i + 1) * frame]) for i in range(n)])
    loudest = float(np.percentile(levels, 95))
    info["loudest_db"] = round(loudest, 1)
    # Only an utterance that is quiet in ABSOLUTE terms counts as "nothing said". Whether it
    # is speech or just noise is decided by Whisper's own speech detector (Silero VAD),
    # which copes with noisy mics far better than a loudness threshold.
    if loudest < ABS_SILENCE_DB:
        info["too_quiet"] = True
        return x, info
    # The calibrated gate only TRIMS silence. It can never sit above this utterance's own
    # speech level, so a gate calibrated too high (noisy mic, noise spike during calibration,
    # someone else's settings) can't throw speech away.
    gate = min(gate_db, loudest - GATE_BELOW_SPEECH_DB)
    info["gate_used_db"] = round(gate, 1)
    loud = np.where(levels > gate)[0]
    info["speech_ms"] = int(len(loud) * BLOCK_MS)
    if len(loud) < 3:                           # < 60 ms of anything: a click, not speech
        info["too_quiet"] = True
        return x, info
    pad = int(250 / BLOCK_MS)
    a = max(0, loud[0] - pad) * frame
    b = min(n, loud[-1] + 1 + pad) * frame
    x = x[a:b]
    peak = float(np.max(np.abs(x))) or 1e-9
    info["peak_db"] = 20 * np.log10(peak)
    # Normalise to -3 dBFS but never boost more than 20 dB (that would just amplify hiss).
    boost = min(10 ** (-3 / 20) / peak, 10.0)
    x = np.clip(x * boost, -1.0, 1.0)
    # Whisper likes a little silence around speech.
    sil = np.zeros(int(TARGET_SR * 0.15), dtype=np.float32)
    return np.concatenate([sil, x.astype(np.float32), sil]), info
