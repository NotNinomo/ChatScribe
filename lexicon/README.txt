ChatScribe lexicon files
========================

Every *.txt file in this folder is loaded at startup (this README is skipped).
Add your own words to custom.txt - guild names, friends, alts, new Forever zones.

Line format
-----------
    Canonical Spelling
    Canonical Spelling = misheard one; misheard two; another
    !ABBR = spoken form; other form
    # comment

- "Canonical Spelling" is exactly how the word will be written in chat.
- After "=" list things the recognizer tends to write instead (lowercase is fine).
  Those are replaced exactly, wherever they appear as whole words.
- Names that are not normal English words are ALSO matched by sound
  (fuzzy phonetic matching), so "on kirage" still becomes "Ahn'Qiraj"
  even if you never listed it.
- Prefix a line with "!" to turn OFF sound-matching for that entry
  (use this for short abbreviations and anything that is also a normal word).
- ALL-CAPS abbreviations automatically accept spelled-out forms:
  "LFG" also matches "l f g", "l.f.g." and "l-f-g".

Section headers like "[Zones]" are just for humans; they are ignored.
