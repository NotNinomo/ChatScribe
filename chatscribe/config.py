"""Settings, stored as JSON next to the app (config.json)."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field, fields

from . import APP_DIR

log = logging.getLogger(__name__)
CONFIG_PATH = APP_DIR / "config.json"


@dataclass
class Binding:
    """A push-to-talk key. `prefix` is put in front of the message, e.g. "/p " for party."""
    key: str = "key:ctrl_r"
    prefix: str = ""
    label: str = "Chat"


@dataclass
class Config:
    # --- push to talk
    bindings: list = field(default_factory=lambda: [asdict(Binding())])
    mode: str = "hold"                  # "hold" (talk while held) or "toggle" (press, talk, press)
    suppress_hotkey: bool = False       # stop the PTT key from also reaching the game

    # --- audio
    input_device: str = ""              # "" = Windows default microphone
    gain: float = 1.0                   # manual input gain (calibration sets this)
    gate_db: float = -50.0              # frames quieter than this are treated as silence
    noise_db: float = -65.0             # measured background noise (calibration)
    speech_db: float = -25.0            # measured speaking level (calibration)
    preroll_ms: int = 300               # audio kept from just before you pressed the key
    tail_ms: int = 250                  # keep listening this long after you release
    max_seconds: int = 45

    # --- recognition
    model: str = "large-v3-turbo"       # large-v3-turbo (best+fast on GPU), distil-large-v3, small.en, ...
    device: str = "auto"                # auto | cuda | cpu
    language: str = "en"
    beam_size: int = 5

    # --- text
    style: str = "natural"              # proper | natural | casual
    spoken_punctuation: bool = True     # "question mark", "comma", "smiley face" ...
    remove_fillers: bool = True         # um, uh, erm
    shorthand: bool = False             # "looking for group" -> LFG, "be right back" -> brb
    voice_channels: bool = True         # "party chat ...", "whisper Name ...", "slash dance"
    channel_numbers: dict = field(default_factory=lambda: {
        "general": 1, "trade": 2, "local defense": 3, "looking for group": 4, "lfg": 4,
        "world": 5,
    })

    # --- output
    auto_send: bool = True              # press Enter after pasting
    open_chat_first: bool = True        # press Enter to open the chat box before pasting
    only_when_wow_focused: bool = True  # otherwise just copy to clipboard
    wow_processes: list = field(default_factory=lambda: [
        "wow.exe", "wowclassic.exe", "wowt.exe", "wowb.exe"])
    restore_clipboard: bool = True
    key_delay_ms: int = 35

    # --- ui
    always_on_top: bool = True
    overlay: bool = True                # small "Listening..." badge at the top of the screen
    history_size: int = 30

    @classmethod
    def load(cls) -> "Config":
        cfg = cls()
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                for k, v in data.items():
                    if k in known:
                        setattr(cfg, k, v)
            except Exception as e:
                log.error("config.json is broken (%s); using defaults", e)
        else:
            cfg.save()
        # A hand-edited / old config must never crash startup.
        good = []
        for b in cfg.bindings if isinstance(cfg.bindings, list) else []:
            if isinstance(b, dict) and isinstance(b.get("key"), str) and ":" in b["key"]:
                good.append({"key": b["key"], "prefix": str(b.get("prefix", "")),
                             "label": str(b.get("label", "Chat"))})
        cfg.bindings = good or [asdict(Binding())]
        return cfg

    def save(self) -> None:
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
