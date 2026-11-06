"""The controller: hotkeys -> recorder -> Whisper -> formatter -> WoW chat.

Everything slow runs on worker threads; the UI only reads events from `self.events`.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

from .audio import Recorder, prepare
from .config import Config
from .engine import Engine
from .hotkey import HotkeyManager, describe
from .lexicon import Lexicon
from .textproc import ChatLine, Formatter
from .typer import ChatSender

log = logging.getLogger(__name__)


@dataclass
class Result:
    raw: str
    line: ChatLine
    lines: list
    status: str
    timings: dict = field(default_factory=dict)
    binding: int = 0


class ChatScribe:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config.load()
        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.lexicon = Lexicon(shorthand=self.cfg.shorthand)
        self.formatter = Formatter(self.lexicon, self.cfg)
        self.sender = ChatSender(self.cfg)
        self.engine = Engine(self.cfg.model, self.cfg.device, language=self.cfg.language,
                             beam_size=self.cfg.beam_size)
        self.recorder = Recorder(self.cfg.input_device, self.cfg.preroll_ms, self.cfg.tail_ms,
                                 self.cfg.max_seconds)
        self.hotkeys = HotkeyManager(self._on_down, self._on_up)
        self.ready = False
        self.paused = False
        self.active_binding: int | None = None
        self.capture_hook = None     # calibration: callable(raw, final_line) instead of sending
        self._talking = False        # push-to-talk is down (independent of the recorder state)
        self._work = queue.Queue()
        self._busy = threading.Lock()

    # ----------------------------------------------------------- lifecycle
    def start(self) -> None:
        try:
            self.recorder.open()
        except Exception as e:
            self.emit("error", str(e))
        self.apply_bindings()
        self.hotkeys.start()
        threading.Thread(target=self._load_model, daemon=True).start()
        threading.Thread(target=self._worker, daemon=True).start()

    def shutdown(self) -> None:
        self.hotkeys.stop()
        self.recorder.close()

    def _load_model(self) -> None:
        self.ready = False
        self.emit("status", "loading", "Loading speech model...")
        try:
            self.engine.load(lambda m: self.emit("status", "loading", m))
            self.ready = True
            self.emit("status", "ready", self.ready_text())
        except Exception as e:
            log.exception("model load")
            self.emit("error", f"Speech model failed to load: {e}")

    def reload_model(self) -> None:
        self.engine = Engine(self.cfg.model, self.cfg.device, language=self.cfg.language,
                             beam_size=self.cfg.beam_size)
        threading.Thread(target=self._load_model, daemon=True).start()

    def reopen_mic(self) -> None:
        self.recorder.close()
        self.recorder.device_name = self.cfg.input_device
        self.recorder.preroll_ms = self.cfg.preroll_ms
        self.recorder.tail_ms = self.cfg.tail_ms
        try:
            self.recorder.open()
        except Exception as e:
            self.emit("error", str(e))

    def apply_bindings(self) -> None:
        self.hotkeys.set_bindings([b["key"] for b in self.cfg.bindings], self.cfg.suppress_hotkey)

    def ready_text(self) -> str:
        keys = " / ".join(describe(b["key"]) for b in self.cfg.bindings)
        verb = "Hold" if self.cfg.mode == "hold" else "Press"
        return f"Ready - {verb} {keys} to talk"

    def emit(self, *ev) -> None:
        self.events.put(ev)

    # ------------------------------------------------------------- hotkeys
    def _on_down(self, idx: int) -> None:
        if self.paused:
            return
        if not self.ready:
            self.emit("status", "loading", "Still loading the speech model...")
            return
        if self.cfg.mode == "toggle" and self._talking:
            self._finish()
            return
        if self._talking or self.recorder.recording:
            return
        if self.recorder.stream is None:
            self.emit("error", "No microphone - open Settings and pick one.")
            return
        self.active_binding = idx
        self._talking = True
        self.recorder.begin()
        self.emit("status", "listening", "Listening...")

    def _on_up(self, idx: int) -> None:
        # Uses _talking, not recorder.recording: the recorder stops by itself at max_seconds,
        # and that audio must still be transcribed when the key comes up.
        if self.cfg.mode == "hold" and self._talking and idx == self.active_binding:
            self._finish()

    def _finish(self) -> None:
        if not self._busy.acquire(blocking=False):
            return                              # already finishing this utterance
        self._talking = False
        self.recorder.request_stop()
        self.emit("status", "thinking", "Transcribing...")
        self._work.put(self.active_binding or 0)

    # -------------------------------------------------------------- worker
    def _worker(self) -> None:
        while True:
            idx = self._work.get()
            try:
                self._process(idx)
            except Exception as e:
                log.exception("processing failed")
                self.emit("error", f"Something went wrong: {e}")
                self.emit("status", "ready", self.ready_text())

    def _process(self, idx: int) -> None:
        t0 = time.perf_counter()
        try:
            audio = self.recorder.collect()
        finally:
            self._busy.release()                # mic is free for the next utterance
        clean, info = prepare(audio, self.cfg.gain, self.cfg.gate_db)
        if info["too_quiet"]:
            self.emit("status", "ready", "Didn't hear anything - " + self.ready_text())
            self.emit("meter_hint", "quiet")
            if self.capture_hook:
                self.capture_hook("", None)
            return
        prompt = self.build_prompt()
        tr = self.engine.transcribe(clean, prompt=prompt)
        t1 = time.perf_counter()
        binding = self.cfg.bindings[idx] if idx < len(self.cfg.bindings) else {}
        line = (self.formatter.process(tr.text, binding.get("prefix", "")) if tr.text
                else ChatLine(cancelled=True))
        if self.capture_hook:
            self.capture_hook(tr.text, line)
            self.emit("status", "ready", self.ready_text())
            return
        if not tr.text:
            self.emit("status", "ready", "Couldn't make that out - " + self.ready_text())
            return
        if line.cancelled:
            self.emit("result", Result(tr.text, line, [], "cancelled", {}, idx))
            self.emit("status", "ready", "Cancelled - " + self.ready_text())
            return
        if not line.prefix and binding.get("prefix"):
            line.prefix = binding["prefix"]
            line.route_label = binding.get("label", "")
        lines = line.lines()
        status = self.sender.send(lines)
        t2 = time.perf_counter()
        timings = {"audio": round(tr.audio_seconds, 2), "recognize": round(t1 - t0, 2),
                   "total": round(t2 - t0, 2)}
        self.emit("result", Result(tr.text, line, lines, status, timings, idx))
        self.emit("status", "sent", f"{status.capitalize()} ({timings['recognize']}s)")

    def build_prompt(self) -> str:
        terms = self.lexicon.prompt_terms()
        return ("World of Warcraft chat. Hey, anyone want to group up for Stratholme? "
                "Need a tank and a healer, then we head to Orgrimmar. " + ", ".join(terms) + ".")

    # ---------------------------------------------------------- utilities
    def teach(self, heard: str, correct: str) -> bool:
        return self.lexicon.learn(heard, correct)
