"""Whisper transcription engine (faster-whisper / CTranslate2) with GPU setup."""

from __future__ import annotations

import logging
import os
import site
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000


def _register_cuda_dlls() -> None:
    """Make the NVIDIA DLLs (cuBLAS, cuDNN) visible to CTranslate2 on Windows -
    from pip's site-packages when run from source, or from the bundle when packaged."""
    if sys.platform != "win32":
        return
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)))
    roots += [Path(p) for p in site.getsitepackages()]
    try:
        roots.append(Path(site.getusersitepackages()))
    except Exception:
        pass
    for root in roots:
        nv = root / "nvidia"
        if not nv.is_dir():
            continue
        for bin_dir in nv.glob("*/bin"):
            try:
                os.add_dll_directory(str(bin_dir))
            except (OSError, AttributeError):
                pass
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


def resolve_model(name: str) -> str:
    """A model folder shipped next to the app (models/<name>) wins over the HF cache."""
    from . import MODELS_DIR
    local = MODELS_DIR / name
    if (local / "model.bin").is_file():
        return str(local)
    return name


_register_cuda_dlls()

from faster_whisper import WhisperModel  # noqa: E402  (must come after DLL setup)


@dataclass
class Transcript:
    text: str
    raw_segments: list = field(default_factory=list)
    avg_logprob: float = 0.0
    no_speech_prob: float = 0.0
    seconds: float = 0.0
    audio_seconds: float = 0.0
    words: list = field(default_factory=list)  # (word, probability)


# Phrases Whisper is known to invent on silence / noise. Matched case-insensitively
# against the whole utterance after stripping punctuation.
HALLUCINATIONS = {
    "thank you", "thanks for watching", "thank you for watching", "thank you so much",
    "please subscribe", "subscribe", "bye", "you", "thanks", "okay", "so",
    "subtitles by the amaraorg community", "transcription by castingwords",
    "thank you for watching and ill see you next time", "the end",
    "please like and subscribe", "music", "applause", "silence",
}


class Engine:
    def __init__(self, model_name: str = "large-v3-turbo", device: str = "auto",
                 compute_type: str = "auto", language: str = "en", beam_size: int = 5):
        self.model_name = model_name
        self.language = language or None
        self.beam_size = beam_size
        self.device = device
        self.compute_type = compute_type
        self.model: WhisperModel | None = None
        self.actual_device = "?"

    # ------------------------------------------------------------------ load
    def load(self, progress=lambda msg: None) -> None:
        attempts = []
        if self.device in ("auto", "cuda"):
            attempts.append(("cuda", "float16" if self.compute_type == "auto" else self.compute_type))
            attempts.append(("cuda", "int8_float16"))
        if self.device in ("auto", "cpu"):
            attempts.append(("cpu", "int8" if self.compute_type == "auto" else self.compute_type))
        last_err = None
        for dev, ct in attempts:
            try:
                progress(f"Loading {self.model_name} on {dev} ({ct})... first run downloads the model")
                src = resolve_model(self.model_name)
                try:
                    # Already downloaded? Load offline - no network request at startup.
                    self.model = WhisperModel(src, device=dev, compute_type=ct,
                                              local_files_only=True)
                except Exception as e:
                    msg = str(e).lower()
                    if src != self.model_name or not any(k in msg for k in
                                                         ("local", "find", "snapshot", "cache")):
                        raise
                    progress(f"Downloading {self.model_name} (first run only)...")
                    self.model = WhisperModel(src, device=dev, compute_type=ct)
                self.actual_device = f"{dev}/{ct}"
                # Warm up so the first real utterance is fast (CUDA kernels, cuDNN autotune).
                self._run(np.zeros(SAMPLE_RATE, dtype=np.float32), prompt=None, hotwords=None)
                progress(f"Model ready on {self.actual_device}")
                return
            except Exception as e:  # pragma: no cover - hardware dependent
                last_err = e
                log.warning("Model load failed on %s/%s: %s", dev, ct, e)
        raise RuntimeError(f"Could not load Whisper model: {last_err}")

    # ------------------------------------------------------------ transcribe
    def _run(self, audio: np.ndarray, prompt: str | None, hotwords: str | None):
        assert self.model is not None
        segments, info = self.model.transcribe(
            audio,
            language=self.language,
            beam_size=self.beam_size,
            best_of=5,
            temperature=(0.0, 0.2, 0.4, 0.6),
            initial_prompt=prompt,
            hotwords=hotwords,
            condition_on_previous_text=False,
            without_timestamps=True,        # timestamps/word alignment cost time; unused
            word_timestamps=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300,
                            "threshold": 0.35},
            no_speech_threshold=0.6,
            log_prob_threshold=-1.0,
            compression_ratio_threshold=2.4,
            suppress_blank=True,
        )
        return list(segments), info

    def transcribe(self, audio: np.ndarray, prompt: str | None = None,
                   hotwords: str | None = None) -> Transcript:
        t0 = time.perf_counter()
        segs, _info = self._run(audio.astype(np.float32), prompt, hotwords)
        kept = []
        for s in segs:
            # Drop segments Whisper itself thinks are not speech.
            if s.no_speech_prob > 0.7 and s.avg_logprob < -0.8:
                continue
            kept.append(s)
        text = " ".join(s.text.strip() for s in kept).strip()
        tr = Transcript(
            text=text,
            raw_segments=kept,
            avg_logprob=float(np.mean([s.avg_logprob for s in kept])) if kept else -9.0,
            no_speech_prob=float(np.max([s.no_speech_prob for s in kept])) if kept else 1.0,
            seconds=time.perf_counter() - t0,
            audio_seconds=len(audio) / SAMPLE_RATE,
        )
        if is_hallucination(tr):
            log.info("Discarded likely hallucination: %r", tr.text)
            tr.text = ""
        return tr


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").strip()


def is_hallucination(tr: Transcript) -> bool:
    t = _norm(tr.text)
    if not t:
        return False
    if t in HALLUCINATIONS and (tr.no_speech_prob > 0.2 or tr.avg_logprob < -0.6
                                or tr.audio_seconds < 1.2):
        return True
    # Repetition loops, e.g. "the the the the the"
    words = t.split()
    if len(words) >= 8 and len(set(words)) <= 2:
        return True
    return False
