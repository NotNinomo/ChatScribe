"""Run:  .venv\\Scripts\\python -m pytest tests -q   (or just: python tests\\test_text.py)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chatscribe.config import Config  # noqa: E402
from chatscribe.lexicon import Lexicon  # noqa: E402
from chatscribe.textproc import Formatter  # noqa: E402

CFG = Config()
LEX = Lexicon()
FMT = Formatter(LEX, CFG)


def fmt(raw, **over):
    for k, v in over.items():
        setattr(CFG, k, v)
    try:
        line = FMT.process(raw)
        return line.lines()
    finally:
        for k in over:
            setattr(CFG, k, getattr(Config(), k))


CASES = [
    # (raw recogniser text, expected chat line(s))
    ("anyone want to run on kiraj tonight", ["Anyone want to run Ahn'Qiraj tonight?"]),
    ("Heading to Orgrimar then the barons.", ["Heading to Orgrimmar then the Barrens"]),
    ("need a tank for zool gurub", ["Need a tank for Zul'Gurub"]),
    ("L.F.G. U.B.R.S. need heals", ["LFG UBRS need heals"]),
    ("um so uh who has thunder fury", ["So who has Thunderfury?"]),
    ("I fear that the alliance will win.", ["I fear that the alliance will win"]),
    ("ragnaroc is up", ["Ragnaros is up"]),
    ("Party chat, pulling in 5 seconds.", ["/p Pulling in 5 seconds"]),
    ("party is ready", ["Party is ready"]),
    ("tell guild gz on the drop", ["/g Gz on the drop"]),
    ("Whisper thrall, want to group up?", ["/w Thrall Want to group up?"]),
    ("raid warning stack on the tank", ["/rw Stack on the tank"]),
    ("Slash dance.", ["/dance"]),
    ("slash roll", ["/roll"]),
    ("slash invite bob", ["/invite bob"]),
    ("emote waves hello", ["/e waves hello"]),
    ("Trade chat, WTS Black Lotus, PST.", ["/2 WTS Black Lotus, PST"]),
    ("what's up question mark", ["What's up?"]),
    ("nice smiley face", ["Nice :)"]),
    ("we wiped scratch that", []),
    ("Cancel.", []),
    ("can't wait for Naxx", ["Can't wait for Naxx"]),
    ("have fun guys", ["Have fun guys"]),
    ("do you need anything from Gadgetzan", ["Do you need anything from Gadgetzan?"]),
    ("i'm oom", ["I'm OOM"]),
    ("going to Strath home", ["Going to Stratholme"]),
    ("the the tank is afk", ["The tank is afk"]),
    ("we need more orcs", ["We need more orcs"]),
    ("Heading to On'Goro Crater and Silithus.", ["Heading to Un'Goro Crater and Silithus"]),
    ("LFGUBRS, Need Heels", ["LFG UBRS, need heals"]),
    ("Party chat. Pulling in five seconds.", ["/p Pulling in 5 seconds"]),
    ("level sixty rogue LF raid at eight tonight", ["Level 60 rogue LF raid at 8 tonight"]),
    ("need one healer and two dps", ["Need one healer and two DPS"]),
    ("I have the Hawker quest", ["I have the Hakkar quest"]),
    ("Kel'uzad and Safiran are in Nakekramas.", ["Kel'Thuzad and Sapphiron are in Naxxramas"]),
    ("Whisper Thrall, want to run Dire Maul.", ["/w Thrall Want to run Dire Maul?"]),
    ("the last boss is hard", ["The last boss is hard"]),
    ("my rouge is level 60", ["My rogue is level 60"]),
    ("A pipe | char", ["A pipe / char"]),
    # --- must NOT switch channel (normal speech that starts like a command)
    ("In the raid tonight we need more healers.", ["In the raid tonight we need more healers"]),
    ("in party we trust", ["In party we trust"]),
    ("Yell if you need help.", ["Yell if you need help"]),
    ("reply to him later", ["Reply to him later"]),
    ("Party chat is dead tonight.", ["Party chat is dead tonight"]),
    ("Trade chat was crazy today.", ["Trade chat was crazy today"]),
    ("In general chat everyone is arguing", ["In general chat everyone is arguing"]),
    ("emotional damage", ["Emotional damage"]),
    ("I just want to forget it", ["I just want to forget it"]),
    # --- must switch
    ("Yell, inc!", ["/y Inc!"]),
    # abbreviations that spell real words stay words unless spelled out
    ("nice bow", ["Nice bow"]),
    ("it's so hot in Tanaris", ["It's so hot in Tanaris"]),
    ("connect the dots", ["Connect the dots"]),
    ("we caught a cod", ["We caught a cod"]),
    ("ah nice", ["Ah nice"]),
    ("need b o w and b o k", ["Need BoW and BoK"]),
    ("keep the HoT on the tank", ["Keep the HoT on the tank"]),
    ("my hp is low and I need xp", ["My HP is low and I need XP"]),
    ("lf tank", ["LF tank"]),
    ("Anyone want to do the dead mines? Question mark.", ["Anyone want to do the Deadmines?"]),
    ("Summon 2 Zul'Goroob please", ["Summon to Zul'Gurub please"]),
    ("I need 2 healers", ["I need 2 healers"]),
    ("hardest bosses in Nakechramas", ["Hardest bosses in Naxxramas"]),
    ("Reply, sure thing.", ["/r Sure thing"]),
    ("General chat, anyone selling bags?", ["/1 Anyone selling bags?"]),
]


def test_cases():
    bad = []
    for raw, want in CASES:
        got = fmt(raw)
        if got != want:
            bad.append(f"{raw!r}\n   want {want!r}\n   got  {got!r}")
    assert not bad, "\n" + "\n".join(bad)


def test_styles():
    assert fmt("heading to ironforge.", style="proper") == ["Heading to Ironforge."]
    assert fmt("Heading to Ironforge.", style="casual") == ["heading to Ironforge"]
    assert fmt("Orgrimmar is busy.", style="casual") == ["Orgrimmar is busy"]


def test_shorthand():
    assert fmt("looking for group for deadmines", shorthand=True) == ["LFG for Deadmines"]
    assert fmt("looking for group for deadmines") == ["Looking for group for Deadmines"]
    assert fmt("be right back", shorthand=True) == ["Brb"]


def test_split_long():
    long = "word " * 120
    lines = fmt(long)
    assert len(lines) >= 2 and all(len(x) <= 255 for x in lines)


if __name__ == "__main__":
    fails = 0
    for raw, want in CASES:
        got = fmt(raw)
        ok = got == want
        fails += not ok
        print(("OK  " if ok else "FAIL"), repr(raw), "->", got, "" if ok else f"(want {want})")
    for t in (test_styles, test_shorthand, test_split_long):
        try:
            t()
            print("OK  ", t.__name__)
        except AssertionError as e:
            fails += 1
            print("FAIL", t.__name__, e)
    print(f"\n{fails} failures")
