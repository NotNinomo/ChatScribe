# Changelog

## 1.1.0
- Packaged Windows build (`ChatScribe.exe`); release zip downloads the model on first run.
- Model loads offline after the first download - no network requests at startup.
- About 30% faster recognition (no unused word timestamps).
- Channel commands only trigger on deliberate commands ("party chat, ...", "tell guild ...");
  "in the raid tonight ..." and "yell if you need help" stay normal chat.
- "+ Add key" asks for the key instead of inventing a Mouse 5 -> Party binding.
- Clipboard restore can no longer paste your previous clipboard into chat.
- Push-to-talk hook survives errors; overlay can't steal focus from WoW.
- Utterances longer than the max length are transcribed instead of lost.
- Fixes: double "??", "Summon 2 Zul'Gurub", readable Settings dropdowns, meter/gate mismatch.

## 1.0.0
- First version: push-to-talk Whisper, WoW lexicon with sound-alike matching, punctuation,
  voice channel commands, calibration that learns your voice, Classic-styled UI.
