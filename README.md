<div align="center">

<img src="assets/icon.png" width="96" alt="ChatScribe icon">

# ChatScribe

**Talk, and it types into WoW chat, with the names spelled right.**

Push-to-talk speech-to-text for **World of Warcraft: Forever**. It runs 100% locally on your own GPU.

[Download](../../releases/latest) · [How it works](#how-it-works) · [Build from source](#build-from-source) · [Add your own words](#add-your-own-words)

<img src="docs/screenshot.png" width="460" alt="ChatScribe window">

</div>

---

Hold a key, say *"anyone want to run on kiraj tonight"*, let go. Your chat gets:

```
Anyone want to run Ahn'Qiraj tonight?
```

Normal dictation tools mangle Azeroth: you get "on kiraj", "zool gurub" and "Nakekramas". ChatScribe pairs Whisper (an open-source speech model) with a word list of over 1,200 WoW terms and a phonetic matcher built for fantasy names, so names come out the way players actually write them.

## Features

- **Fast and private.** Whisper large-v3-turbo runs on your NVIDIA GPU and turns a line around in about 0.25 s. No account, no cloud, and no network traffic after the first model download.
- **Knows WoW.** Its word list covers zones, cities, dungeons, raids, bosses, NPCs, lore characters, factions, classes, spells, items, herbs, consumables, professions and chat slang:
  - `L F G U B R S` → `LFG UBRS`
  - "Safiran" → `Sapphiron`
  - "the barons" → `the Barrens`
- **Leaves normal English alone.** "I fear the alliance will win" stays exactly as you said it.
- **Learns your voice.** A 2-minute calibration has you read WoW lines and remembers how *your* voice gets misheard. You can also right-click any message and choose **Fix a word** to teach it a correction.
- **Real chat punctuation.** It adds question marks on its own and handles spoken punctuation ("question mark", "smiley face" → `:)`). It removes um and uh and writes chat-style numbers ("level sixty" → `level 60`). Messages longer than 255 characters are split automatically.
- **Voice channel commands:**

  | Say | Sends |
  |---|---|
  | "party chat, pulling in five seconds" | `/p Pulling in 5 seconds` |
  | "tell guild Onyxia at eight tonight" | `/g Onyxia at 8 tonight` |
  | "whisper Thrall, want to run Dire Maul" | `/w Thrall Want to run Dire Maul?` |
  | "raid warning, stack on the tank" | `/rw Stack on the tank` |
  | "trade chat, WTS Black Lotus" | `/2 WTS Black Lotus` |
  | "slash dance" | `/dance` |
  | "… scratch that" | *(nothing is sent)* |

  A channel only switches on a deliberate command. "Party is ready" and "in the raid tonight…" stay normal chat.
- **Several push-to-talk keys.** Keyboard keys and mouse side buttons both work, and each key can have its own channel (for example, Mouse 5 → Party).
- **Fits the game.** A dark, gold-trimmed window in the style of the classic UI, plus an optional click-through "Listening" badge.

## Download

1. Grab `ChatScribe-vX.Y.Z-windows.zip` from [Releases](../../releases/latest) and unzip it anywhere.
2. Run **`ChatScribe.exe`**. The first start downloads the speech model (about 1.6 GB, once).
3. Click **Calibrate** and follow the three short steps.
4. In game, hold **Right Ctrl**, talk, and let go.

**Requirements:**
- Windows 10 or 11.
- An NVIDIA GPU is strongly recommended (GTX 10-series or newer, about 3 GB of VRAM).
- Without one, pick the `small.en` model in Settings. It runs on the CPU and is slower and less accurate.

> If WoW runs **as administrator**, ChatScribe must too. Windows blocks programs from sending keys into elevated windows.

## How it works

```
 hold key ──► mic (pre-roll, noise gate, normalise)
          ──► Whisper large-v3-turbo on CUDA, primed with WoW vocabulary
          ──► voice commands      "party chat ..."  "scratch that"
          ──► spoken punctuation  "question mark" → ?
          ──► WoW lexicon         exact aliases + phonetic sound-alike matching
          ──► chat formatting     questions, casing, numbers, 255-char split
          ──► WoW chat box        Enter, paste, Enter (your clipboard is restored)
```

Each stage is a small module under [`chatscribe/`](chatscribe):

| Module | Does |
|---|---|
| `engine.py` | Whisper via faster-whisper / CTranslate2 (CUDA float16, CPU fallback) |
| `audio.py` | Always-open mic stream with pre-roll, calibrated noise gate, normalisation |
| `lexicon.py` | Loads `lexicon/*.txt`; exact alias, glued-acronym and sound-alike correction |
| `phonetics.py` | Phonetic key tuned for fantasy names ("Ahn'Qiraj" ≈ "on kiraj") |
| `textproc.py` | Channel commands, punctuation, questions, casing, numbers, chat style |
| `typer.py` | WoW focus check, clipboard paste via `SendInput`, clipboard restore |
| `hotkey.py` | Global keyboard / mouse push-to-talk via low-level hooks |
| `calibration.py` | Noise and speech level measurement; learns misheard words |
| `ui.py` | Main window, overlay, settings, calibration wizard |

**Accuracy.** [`tools/benchmark.py`](tools/benchmark.py) has three Windows text-to-speech voices read 18 WoW chat lines and runs each one through the same pipeline as the app:

| | Word accuracy | Exact lines |
|---|---|---|
| Whisper alone | 88.4% | — |
| **ChatScribe** | **99.6%** | **53 / 54** |

Median latency is 0.25 s on an RTX 4080. Real voices after calibration should do at least as well.

## Add your own words

Put guild names, friends, alts, and new Forever zones or bosses into **`lexicon/custom.txt`**, which is created on first run. One entry per line, with the misheard versions after `=`:

```
Ironclad Vanguard = iron clad vanguard
Badbluud = bad blood
```

Names that aren't English words are matched by sound automatically, even if you never list a misheard version. See [`lexicon/README.txt`](lexicon/README.txt) for the full format.

## Build from source

You need Python 3.10 or newer on Windows.

```bat
git clone https://github.com/<you>/ChatScribe
cd ChatScribe
scripts\setup.bat            :: creates .venv and installs everything
scripts\run.bat              :: run from source
```

| Task | Command |
|---|---|
| Run with a log console | `scripts\run-debug.bat` |
| Text pipeline tests | `.venv\Scripts\python -m pytest tests -q` |
| End-to-end benchmark | `.venv\Scripts\python tools\benchmark.py` |
| Build `dist\ChatScribe\ChatScribe.exe` | `scripts\build.bat` (bundles the model, works offline) |
| Small build for a release | `scripts\build.bat --no-model --zip` |

Pushing a `v*` tag builds the release zip with GitHub Actions (see [`.github/workflows`](.github/workflows)).

## Files ChatScribe writes

Everything stays inside its own folder:

```
config.json                 settings (from the Settings window)
data/learned_aliases.json   words learned from calibration and "Fix a word"
data/chatscribe.log         log (rotated at 2 MB)
models/                     the speech model
lexicon/custom.txt          your own words
```

## FAQ

**Why not a WoW addon?** Addons run in a sandbox. They can't hear your microphone or receive data from outside the game, so the recognition has to happen outside WoW. ChatScribe then types into the chat box the way you would: one key press gives you one chat line.

**Nothing shows up in chat.**
- Make sure WoW is the active window. Otherwise ChatScribe copies the text to your clipboard instead.
- If WoW runs as administrator, run ChatScribe as administrator too.
- Check `data/chatscribe.log`.

**It types into party chat or the wrong channel.** Check Settings → Push-to-talk keys. Each key has its own channel. Don't use the same button as your Discord push-to-talk.

## License

[MIT](LICENSE). World of Warcraft names in `lexicon/` are used only so the app can spell them correctly.

ChatScribe is a fan-made tool. It is not affiliated with or endorsed by Blizzard Entertainment. World of Warcraft and Warcraft are trademarks of Blizzard Entertainment, Inc. Using any third-party tool with the game is your own responsibility.
