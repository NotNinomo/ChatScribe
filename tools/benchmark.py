"""End-to-end benchmark without a human: Windows text-to-speech reads WoW chat lines,
then each clip goes through prepare -> Whisper -> Formatter, exactly like the live app.

    .venv\\Scripts\\python tools\\benchmark.py            (uses every installed SAPI voice)
    .venv\\Scripts\\python tools\\benchmark.py --no-prompt (to see what the WoW prompt buys)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatscribe.app import ChatScribe  # noqa: E402
from chatscribe.audio import prepare, resample  # noqa: E402
from chatscribe.calibration import word_accuracy  # noqa: E402
from chatscribe.config import Config  # noqa: E402

# (spoken text for TTS, expected chat line). TTS says names "phonetically" like a player would.
CASES = [
    ("Looking for more for Upper Blackrock Spire, need one healer.",
     "Looking for more for Upper Blackrock Spire, need one healer"),
    ("Meet me in Orgrimmar, then we fly to Gadgetzan.",
     "Meet me in Orgrimmar, then we fly to Gadgetzan"),
    ("Who is tanking Ragnaros in Molten Core tonight?",
     "Who is tanking Ragnaros in Molten Core tonight?"),
    ("Selling Black Lotus and Arcanite Bars, whisper me.",
     "Selling Black Lotus and Arcanite Bars, whisper me"),
    ("We wiped on Vaelastrasz again, everyone run back.",
     "We wiped on Vaelastrasz again, everyone run back"),
    ("Is anyone farming Stratholme or Scholomance?",
     "Is anyone farming Stratholme or Scholomance?"),
    ("On Kiraj opens soon and See Thoon is the last boss.",
     "Ahn'Qiraj opens soon and C'Thun is the last boss"),
    ("Summon to Zool Guroob please, I have the Hakkar quest.",
     "Summon to Zul'Gurub please, I have the Hakkar quest"),
    ("Party chat, pulling in five seconds.", "/p Pulling in 5 seconds"),
    ("Whisper Thrall, want to run Dire Maul?", "/w Thrall Want to run Dire Maul?"),
    ("Kel Thoozad and Sapphiron are the hardest bosses in Naxxramas.",
     "Kel'Thuzad and Sapphiron are the hardest bosses in Naxxramas"),
    ("Heading to Un Goro Crater and Silithus after Tanaris.",
     "Heading to Un'Goro Crater and Silithus after Tanaris"),
    ("L F G U B R S, need heals.", "LFG UBRS, need heals"),
    ("Anyone want to do the Deadmines? question mark", "Anyone want to do the Deadmines?"),
    ("Thanks for the group, see you in Stormwind smiley face",
     "Thanks for the group, see you in Stormwind :)"),
    ("Drakkisath dropped nothing again, scratch that", ""),
    ("Tell guild, Onyxia at eight tonight.", "/g Onyxia at 8 tonight"),
    ("My rogue needs Thunderfury, Blessed Blade of the Windseeker.",
     "My rogue needs Thunderfury, Blessed Blade of the Windseeker"),
]


def voices() -> list[str]:
    ps = ("Add-Type -AssemblyName System.Speech; "
          "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices() | "
          "% { $_.VoiceInfo.Name }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True,
                         text=True).stdout
    return [v.strip() for v in out.splitlines() if v.strip()]


def synth(text: str, voice: str, path: Path, rate: int = 0) -> None:
    t = text.replace("'", "''")
    ps = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{voice}'); $s.Rate = {rate}; "
          f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{t}'); $s.Dispose()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True,
                   capture_output=True)


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        sr, n, ch, sw = w.getframerate(), w.getnframes(), w.getnchannels(), w.getsampwidth()
        data = np.frombuffer(w.readframes(n), dtype=np.int16 if sw == 2 else np.uint8)
    x = data.astype(np.float32) / (32768.0 if sw == 2 else 128.0)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return resample(x, sr)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-prompt", action="store_true")
    ap.add_argument("--noise", type=float, default=0.004, help="white noise added (mic hiss)")
    ap.add_argument("--gate", type=float, default=-50.0, help="silence gate dB (try -20 = too high)")
    args = ap.parse_args()

    cfg = Config()          # fixed defaults - never the user's own style/keys
    cfg.gain, cfg.gate_db = 1.0, args.gate
    app = ChatScribe(cfg)
    app.engine.load(print)
    vs = voices()
    print("Voices:", vs)
    tmp = Path(tempfile.mkdtemp(prefix="chatscribe_bench_"))
    rng = np.random.default_rng(1)
    tot_raw = tot_fin = exact = n = 0
    times = []
    for vi, voice in enumerate(vs):
        print(f"\n=== {voice} ===")
        for ci, (spoken, expected) in enumerate(CASES):
            wav = tmp / f"v{vi}_{ci}.wav"
            synth(spoken, voice, wav)
            audio = load_wav(wav) * 0.5
            audio = audio + rng.normal(0, args.noise, audio.shape).astype(np.float32)
            clean, info = prepare(audio, cfg.gain, cfg.gate_db)
            if info["too_quiet"]:
                print(f"DROP  gate discarded the clip: {spoken}")
            prompt = None if args.no_prompt else app.build_prompt()
            tr = app.engine.transcribe(clean, prompt=prompt)
            times.append(tr.seconds)
            line = app.formatter.process(tr.text)
            final = " | ".join(line.lines())
            ok = final == expected
            exact += ok
            raw_acc = word_accuracy(expected, tr.text) if expected else float(line.cancelled)
            fin_acc = word_accuracy(expected, final) if expected else float(line.cancelled)
            tot_raw += raw_acc
            tot_fin += fin_acc
            n += 1
            mark = "OK  " if ok else "MISS"
            print(f"{mark} {tr.seconds:4.2f}s  heard: {tr.text}")
            if not ok:
                print(f"           wrote: {final}\n           want:  {expected}")
    print(f"\nExact lines: {exact}/{n}   raw word acc {tot_raw / n:.1%}   "
          f"final word acc {tot_fin / n:.1%}   median latency {np.median(times):.2f}s")


if __name__ == "__main__":
    main()
