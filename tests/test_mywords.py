"""Un-correcting (blocked corrections), forgetting taught words, and /me keys."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatscribe import lexicon as lexmod  # noqa: E402
from chatscribe.config import Config  # noqa: E402
from chatscribe.textproc import Formatter  # noqa: E402


@pytest.fixture
def fmt(tmp_path, monkeypatch):
    # Never touch the real data/ folder.
    monkeypatch.setattr(lexmod, "LEARNED_PATH", tmp_path / "learned.json")
    monkeypatch.setattr(lexmod, "BLOCKED_PATH", tmp_path / "blocked.json")
    lex = lexmod.Lexicon()
    f = Formatter(lex, Config())
    f.lex = lex
    return f


def say(f, raw, prefix=""):
    return f.process(raw, prefix).lines()


def test_block_turns_a_builtin_correction_off(fmt):
    assert say(fmt, "my rouge is level 60") == ["My rogue is level 60"]
    assert fmt.lex.block("rouge")
    assert say(fmt, "my rouge is level 60") == ["My rouge is level 60"]
    # survives a restart
    assert lexmod.Lexicon().blocked == {"rouge"}
    assert fmt.lex.unblock("rouge")
    assert say(fmt, "my rouge is level 60") == ["My rogue is level 60"]


def test_block_sound_alike_name(fmt):
    assert say(fmt, "I have the Hawker quest") == ["I have the Hakkar quest"]
    fmt.lex.block("Hawker")
    assert say(fmt, "I have the Hawker quest") == ["I have the Hawker quest"]


def test_block_multiword_alias(fmt):
    assert say(fmt, "meet at the barons") == ["Meet at the Barrens"]
    fmt.lex.block("the barons")
    assert say(fmt, "meet at the barons") == ["Meet at the barons"]


def test_blocked_word_inside_longer_alias(fmt):
    fmt.lex.block("barons")
    assert say(fmt, "meet at the barons") == ["Meet at the barons"]
    assert say(fmt, "heading to orgrimar") == ["Heading to Orgrimmar"]   # others still work


def test_forget_taught_word(fmt):
    assert fmt.lex.learn("bad blood", "Badbluud")
    assert say(fmt, "invite bad blood") == ["Invite Badbluud"]
    assert fmt.lex.forget("bad blood")
    assert say(fmt, "invite bad blood") == ["Invite bad blood"]


def test_blocking_a_taught_word_also_forgets_it(fmt):
    fmt.lex.learn("bad blood", "Badbluud")
    fmt.lex.block("bad blood")
    assert "bad blood" not in fmt.lex.learned
    assert say(fmt, "invite bad blood") == ["Invite bad blood"]


def test_teaching_again_unblocks(fmt):
    fmt.lex.block("bad blood")
    fmt.lex.learn("bad blood", "Badbluud")
    assert "bad blood" not in fmt.lex.blocked


def test_me_key_formats_as_emote(fmt):
    line = fmt.process("Waves at the crowd.", "/me ")
    line.prefix = "/me "                        # what the app does for the key's channel
    assert line.lines() == ["/me waves at the crowd"]


def test_me_key_keeps_names_capitalised(fmt):
    line = fmt.process("Thrall nods.", "/me ")
    line.prefix = "/me "
    assert line.lines() == ["/me Thrall nods"]


def test_normal_key_unchanged(fmt):
    assert say(fmt, "waves at the crowd.") == ["Waves at the crowd"]


# --------------------------------------------------------- auto question marks off
@pytest.fixture
def noq(fmt):
    fmt.cfg.auto_question_marks = False
    return fmt


def test_auto_question_marks_on_by_default(fmt):
    assert say(fmt, "anyone want to run deadmines") == ["Anyone want to run Deadmines?"]


def test_no_auto_question_mark(noq):
    assert say(noq, "anyone want to run deadmines") == ["Anyone want to run Deadmines"]
    # Whisper's own "?" is dropped too
    assert say(noq, "Anyone want to run Deadmines?") == ["Anyone want to run Deadmines"]
    assert say(noq, "Is he there? I need a heal.") == ["Is he there. I need a heal"]
    assert say(noq, "Really?!") == ["Really!"]


def test_spoken_question_mark_still_works(noq):
    assert say(noq, "anyone want to run deadmines question mark") == [
        "Anyone want to run Deadmines?"]
    assert say(noq, "Anyone want to run Deadmines? Question mark.") == [
        "Anyone want to run Deadmines?"]


# ------------------------------------------------------------------- yells
def test_yell_formatting(fmt):
    def yell(raw):
        return fmt.process(raw, "/y ").text
    assert yell("inc left side") == "INC LEFT SIDE!"
    assert yell("Run away.") == "RUN AWAY!"
    assert yell("where are you") == "WHERE ARE YOU?!"
    assert yell("For the Horde!") == "FOR THE HORDE!"
    assert yell("we did it smiley face") == "WE DID IT :)"
    assert yell("meet at the barons") == "MEET AT THE BARRENS!"


def test_spoken_yell_shouts(fmt):
    line = fmt.process("yell, inc left side")
    assert line.prefix == "/y " and line.text == "INC LEFT SIDE!"


def test_party_key_not_shouting(fmt):
    assert fmt.process("inc left side", "/p ").text == "Inc left side"


def test_yell_caps_off(fmt):
    fmt.cfg.yell_caps = False
    assert fmt.process("inc left side", "/y ").text == "Inc left side"
