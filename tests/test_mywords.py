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
