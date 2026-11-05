"""Turns raw recogniser output into a clean WoW chat line.

Pipeline:  commands (cancel / channel / slash)  ->  spoken punctuation  ->  fillers
           ->  WoW lexicon  ->  sentence formatting  ->  chat style  ->  WoW-safe text
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .lexicon import Lexicon, _collapse_dotted_acronyms, zipf

CHAT_LIMIT = 255


@dataclass
class ChatLine:
    prefix: str = ""          # "/p ", "/w Name ", "/2 " ...
    text: str = ""
    cancelled: bool = False
    slash: bool = False       # whole line is a slash command
    replacements: list = field(default_factory=list)
    route_label: str = ""

    def lines(self) -> list[str]:
        """Final strings to send, split to WoW's 255 character limit."""
        if self.cancelled or not (self.text or self.slash):
            return []
        if self.slash:
            return [(self.prefix + self.text).strip()[:CHAT_LIMIT]]
        room = CHAT_LIMIT - len(self.prefix)
        return [self.prefix + part for part in split_message(self.text, room)]


# --------------------------------------------------------------------- commands
CANCEL_END = re.compile(
    r"(?:^|[\s,.!?])(scratch that|cancel that|cancel message|delete that|"
    r"never mind that)[\s.,!?]*$", re.I)
CANCEL_WHOLE = re.compile(r"^\W*(cancel|never ?mind|nevermind|scratch that)\W*$", re.I)

# A channel only switches on a deliberate command at the very START of what you said:
#   "<channel> chat ..."  or  "tell / to <channel> ..."   (never "in the raid tonight ...")
# Bare one-word commands ("yell", "reply") also need a pause after them: "Yell, inc!"
_PAUSE = r"\s*[,:;.\-]\s*"
CHANNELS = [
    # (regex at start of utterance, prefix, label)
    (r"(?:raid warning|warn(?:ing)? (?:the )?raid)", "/rw ", "Raid warning"),
    (r"(?:(?:to |tell )(?:the )?party|party chat)", "/p ", "Party"),
    (r"(?:(?:to |tell )(?:the )?raid|raid chat)", "/raid ", "Raid"),
    (r"(?:(?:to |tell )(?:the )?guild|guild chat|g chat|gchat)", "/g ", "Guild"),
    (r"(?:(?:to |tell )(?:the )?officers?|officer chat)", "/o ", "Officer"),
    (r"(?:yell chat|yell(?=" + _PAUSE + r"))", "/y ", "Yell"),
    (r"(?:say chat)", "/s ", "Say"),
    (r"(?:reply(?=" + _PAUSE + r"))", "/r ", "Reply"),
    (r"(?:emote(?=" + _PAUSE + r"|\s+(?:waves|bows|laughs|cheers|dances|nods|points|"
     r"salutes|shrugs|sighs|smiles|thanks|cries|flexes|cheers|is|looks|grins)\b))",
     "/e ", "Emote"),
    (r"(?:battleground chat|bg chat|instance chat)", "/i ", "Instance"),
]
_CHANNEL_RX = [(re.compile(r"^\s*" + p + r"\b[\s,:;.\-]*(?P<rest>.*)$", re.I | re.S), pre, lab)
               for p, pre, lab in CHANNELS]
_NUMBERED_RX = re.compile(r"^\s*(?:to )?(?P<name>[a-z ]+?) chat\b[\s,:;.\-]*(?P<rest>.*)$",
                          re.I | re.S)
# "party chat is dead", "trade chat was crazy" - talking ABOUT a channel, not to it.
_ABOUT_CHANNEL = re.compile(r"^\s*(?:the )?[a-z ]+? chat\s+(?:is|was|has|had|seems|sucks|"
                            r"looks|gets|got|will|keeps|always|never|today|lately|right now)\b",
                            re.I)
_WHISPER_RX = re.compile(
    r"^\s*(?P<verb>whisper|tell|message)\s+(?P<name>[A-Za-z\u00c0-\u024f'\-]+)[\s,:;.\-]+(?P<rest>.+)$",
    re.I | re.S)
_SLASH_RX = re.compile(r"^\s*(?:slash|/)\s*(?P<cmd>[a-z]+)\b[\s,.:]*(?P<rest>.*)$", re.I | re.S)

# Words after "tell" that are channels, not player names.
_NOT_NAMES = {"party", "raid", "guild", "officer", "officers", "the", "them", "him", "her",
              "everyone", "everybody", "you", "me", "us", "all"}

# Only these channel phrases are accepted when the whole utterance starts with a bare word.
# "Party is ready" must NOT be routed to party chat, so bare "party"/"raid" need "chat" or "to".
_BARE_FORBIDDEN = re.compile(r"^\s*(?:party|raid|guild)\s+(?:is|was|will|has|had|are|time)\b",
                             re.I)


def parse_commands(text: str, channel_numbers: dict, enabled: bool = True) -> ChatLine:
    line = ChatLine(text=text.strip())
    if CANCEL_WHOLE.match(line.text):
        line.cancelled = True
        return line
    m = CANCEL_END.search(line.text)
    if m:
        line.cancelled = True
        return line
    if not enabled:
        return line

    t = line.text
    # slash commands: "slash dance", "slash roll", "slash invite Bob"
    m = _SLASH_RX.match(t)
    if m:
        cmd = m.group("cmd").lower()
        rest = m.group("rest").strip().rstrip(".!")
        if cmd in ("p", "party", "g", "guild", "raid", "ra", "rw", "y", "yell", "s", "say",
                   "o", "officer", "e", "emote", "me", "r", "reply", "i", "bg", "instance"):
            line.prefix = f"/{cmd} "
            line.text = rest
            line.route_label = f"/{cmd}"
            return line
        if cmd in ("w", "whisper", "tell", "t") and rest:
            name, _, msg = rest.partition(" ")
            line.prefix = f"/w {_cap(name.strip(',.'))} "
            line.text = msg.strip(" ,")
            line.route_label = f"Whisper {_cap(name)}"
            return line
        line.slash = True
        line.prefix = "/" + cmd + (" " if rest else "")
        line.text = rest
        line.route_label = f"/{cmd}"
        return line

    if _BARE_FORBIDDEN.match(t) or _ABOUT_CHANNEL.match(t):
        return line

    for rx, pre, lab in _CHANNEL_RX:
        m = rx.match(t)
        if m and m.group("rest").strip():
            line.prefix, line.text, line.route_label = pre, m.group("rest").strip(), lab
            return line

    m = _NUMBERED_RX.match(t)
    if m:
        name = m.group("name").strip().lower()
        if name in channel_numbers and m.group("rest").strip():
            line.prefix = f"/{channel_numbers[name]} "
            line.text = m.group("rest").strip()
            line.route_label = f"{name.title()} (/{channel_numbers[name]})"
            return line

    m = _WHISPER_RX.match(t)
    # "whisper X" always; "tell X"/"message X" only when X looks like a name (capitalised).
    if m and m.group("name").lower() not in _NOT_NAMES and (
            m.group("verb").lower() == "whisper" or m.group("name")[0].isupper()):
        name = _cap(m.group("name").strip("'-"))
        line.prefix = f"/w {name} "
        line.text = m.group("rest").strip()
        line.route_label = f"Whisper {name}"
        return line
    return line


def _cap(name: str) -> str:
    return name[:1].upper() + name[1:].lower() if name else name


# ------------------------------------------------------------ spoken punctuation
_SPOKEN = [
    (r"question mark", "?"),
    (r"exclamation (?:mark|point)", "!"),
    (r"full stop", "."),
    (r"semi ?colon", ";"),
    (r"ellipsis|dot dot dot", "..."),
    (r"open (?:paren|parenthesis|bracket)", " ("),
    (r"close (?:paren|parenthesis|bracket)", ")"),
    (r"smiley face|smiley|smile face", " :)"),
    (r"sad face|frowny face", " :("),
    (r"winky face|wink face", " ;)"),
    (r"big grin|grin face", " :D"),
    (r"tongue face|tongue out", " :P"),
    (r"heart emoji|less than three", " <3"),
    (r"x d|ex dee", " xD"),
    (r"o7|salute emoji", " o7"),
]
# (?:'s)? : Whisper sometimes hears "Stormwind smiley face" as "Stormwind's smiley face".
_SPOKEN_RX = [(re.compile(r"(?:'s)?[\s,]*\b(?:" + p + r")\b[.,]?", re.I), rep)
              for p, rep in _SPOKEN]
_COMMA_RX = re.compile(r"[\s,]*\bcomma\b[,.]?", re.I)
_COLON_RX = re.compile(r"[\s,]*\bcolon\b[,.]?", re.I)
# "period" only counts as punctuation at the very end or before "new sentence".
_PERIOD_END_RX = re.compile(r"[\s,]*\bperiod\b[.,]?\s*$", re.I)
_NEW_SENTENCE_RX = re.compile(r"[\s,.]*\b(?:period\s+)?new sentence\b[,.]?\s*", re.I)


def apply_spoken_punctuation(text: str) -> str:
    for rx, rep in _SPOKEN_RX:
        text = rx.sub(rep, text)
    text = _COMMA_RX.sub(",", text)
    text = _COLON_RX.sub(":", text)
    text = _PERIOD_END_RX.sub(".", text)
    text = _NEW_SENTENCE_RX.sub(". ", text)
    return text


# --------------------------------------------------------------------- fillers
_FILLER_RX = re.compile(r"(?:(?<=\s)|^)(?:u+[hm]+|u+h+m+|e+r+m+|e+r+|a+h+m+|hm+|m+h*m+)"
                        r"(?=[\s,.!?]|$)[,.]?", re.I)
_REPEAT_RX = re.compile(r"\b(the|a|an|i|to|and|of|is|it|we|you|for|on|in|that|my)"
                        r"(?:[\s,]+\1\b)+", re.I)


def remove_fillers(text: str) -> str:
    text = _FILLER_RX.sub("", text)
    text = _REPEAT_RX.sub(lambda m: m.group(1), text)
    return text


# ------------------------------------------------------------------ formatting
_PRON = (r"(?:you|u|ya|we|they|he|she|i|it|this|that|these|those|there|anyone|anybody|"
         r"someone|somebody|everyone|any|your|our|my|his|her|their|the|a|an)")
_QUESTION_PATTERNS = [
    re.compile(r"^(?:who|what|when|where|why|how|which|whose|whom)(?:'s|'re|'d)?\b(?! a\b)", re.I),
    re.compile(r"^(?:is|are|was|were|am|isn't|aren't|wasn't|weren't)\b", re.I),
    re.compile(r"^(?:do|does|did|can|could|will|would|should|shall|may|might|have|has|had|"
               r"don't|doesn't|didn't|can't|won't|wouldn't|shouldn't|couldn't|haven't|hasn't)"
               r"\s+" + _PRON + r"\b", re.I),
    re.compile(r"^(?:anyone|anybody|any one|somebody|someone)\b(?!\s+(?:who|that)\b)", re.I),
    re.compile(r"^(?:u|you|ya)\s+(?:want|wanna|need|have|got|coming|ready|there|up for)\b", re.I),
    re.compile(r"^(?:want to|wanna|want a|want some|care to|down to|up for)\b", re.I),
]
_TAG_QUESTION = re.compile(r",\s*(?:right|ok|okay|yeah|correct|or not|or what|no)\s*$", re.I)


_LEAD_RX = re.compile(r"^(?:(?:so|and|but|ok|okay|hey|yo|guys|well|also|oh|alright|"
                      r"btw|quick question|real quick)\b,?\s+)+", re.I)


def _is_question(body: str) -> bool:
    b = _LEAD_RX.sub("", body.strip().strip("\"'("))
    return any(p.match(b) for p in _QUESTION_PATTERNS) or bool(_TAG_QUESTION.search(b))

def tidy_spacing(text: str) -> str:
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2014", " - ")
    text = text.replace("\u2026", "...")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.!?;:)])(?![)(DPp3])", r"\1", text)   # keeps " :)" emoticons
    text = re.sub(r"([(])\s+", r"\1", text)
    text = re.sub(r"([,;:])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(r"(?<![.])([.!?])(?=[A-Za-z])", r"\1 ", text)
    text = re.sub(r",\s*([.!?])", r"\1", text)
    text = re.sub(r"([!?])\.+", r"\1", text)
    text = re.sub(r"\?{2,}", "?", text)     # spoken "question mark" after Whisper's own "?"
    text = re.sub(r"\.{4,}", "...", text)
    text = re.sub(r"(?<!\.)\.\.(?!\.)", ".", text)
    text = re.sub(r"([,;:]){2,}", r"\1", text)
    text = re.sub(r"^[\s,.;:]+", "", text)
    return text.strip()


def _fix_i(text: str) -> str:
    return re.sub(r"\bi\b(?=(?:'m|'ll|'ve|'d|\s|[,.!?]|$))", "I", text)


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?])\s+", text)


def _add_question_marks(text: str) -> str:
    out = []
    for s in _sentences(text):
        core = s.rstrip()
        if not core:
            continue
        if core[-1] in "?!" or re.search(r"[:;]-?[)(DPp]$|<3$|xD$", core):
            out.append(core)
            continue
        body = core.rstrip(".")
        out.append(body + "?" if _is_question(body) else core)
    return " ".join(out)

def _capitalize_sentences(text: str) -> str:
    def cap(m):
        return m.group(1) + m.group(2).upper()
    text = re.sub(r"^(\W*)([a-z])", cap, text)
    text = re.sub(r"([.!?]\s+)([a-z])", cap, text)
    return text


def _decapitalize_first(text: str, protected: set) -> str:
    m = re.match(r"^(\W*)([A-Z][a-z']*)", text)
    if not m:
        return text
    word = m.group(2)
    if word == "I" or word.startswith("I'") or word.lower() in protected:
        return text
    if zipf(word) < 3.0:   # probably a name
        return text
    return text[:m.start(2)] + word[0].lower() + word[1:] + text[m.end(2):]


_NUM_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
_TENS = {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
         "ninety": 90, "twenty": 20}
_NUM_RX = (r"(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ](?:one|two|"
           r"three|four|five|six|seven|eight|nine))?|zero|one|two|three|four|five|six|seven|"
           r"eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|"
           r"nineteen|(?:a |one )?hundred)")
_UNITS = (r"(?:seconds?|secs?|minutes?|mins?|hours?|hrs?|gold|silver|copper|g|man|men|"
          r"percent|stacks?|k|dkp|more|pm|am|o'clock|server time|tonight|ish)")
_NUM_UNIT_RX = re.compile(r"\b(" + _NUM_RX + r")\b(?=\s+" + _UNITS + r"\b)", re.I)
_NUM_AFTER_RX = re.compile(r"\b(level|lvl|rank|tier|t|at|in|by|around|about|for|lf|need)\s+("
                           + _NUM_RX + r")\b(?=\s*(?:[,.!?]|$|" + _UNITS + r"\b|tonight|today))",
                           re.I)
_LEVEL_RX = re.compile(r"\b(level|lvl|rank|tier)\s+(" + _NUM_RX + r")\b", re.I)


def _num_value(words: str) -> str:
    w = words.lower().replace("-", " ").split()
    if w[-1] == "hundred":
        return "100"
    total = 0
    for x in w:
        total += _TENS.get(x, _NUM_WORDS.get(x, 0))
    return str(total)


# Whisper sometimes writes "2" for "to": "summon 2 Zul'Gurub", "heading 2 Org".
_TWO_AS_TO = re.compile(r"\b(summon|summons|port|portal|go|going|head|heading|come|coming|"
                        r"fly|flying|run|running|back|welcome|invite|trying|ready)\s+2\s+"
                        r"(?=[A-Za-z])", re.I)


def numbers_to_digits(text: str) -> str:
    """Chat style numbers: "pulling in five seconds" -> "pulling in 5 seconds",
    "level sixty" -> "level 60", "at eight tonight" -> "at 8 tonight".
    Ordinary counting ("need one healer") is left alone."""
    text = _TWO_AS_TO.sub(lambda m: m.group(1) + " to ", text)
    text = _LEVEL_RX.sub(lambda m: f"{m.group(1)} {_num_value(m.group(2))}", text)
    text = _NUM_UNIT_RX.sub(lambda m: _num_value(m.group(1)), text)
    text = _NUM_AFTER_RX.sub(lambda m: f"{m.group(1)} {_num_value(m.group(2))}", text)
    return text


def fix_stray_capitals(text: str, protected: set) -> str:
    """Whisper sometimes Title-Cases words after a comma ("LFG, Need Heals")."""
    def rep(m):
        word = m.group(2)
        if word.lower() in protected or word == "I" or word.startswith("I'"):
            return m.group(0)
        if zipf(word) >= 4.3:
            return m.group(1) + word.lower()
        return m.group(0)
    # a capitalised word that is NOT at a sentence start
    return re.sub(r"((?<![.!?])(?<![.!?]\s)(?<=[a-z0-9,;:]\s))([A-Z][a-z]+)\b(?!['\-]\w)", rep, text)


def apply_style(text: str, style: str, protected: set) -> str:
    if not text:
        return text
    text = fix_stray_capitals(text, protected)
    text = _fix_i(text)
    text = _add_question_marks(text)
    text = _capitalize_sentences(text)
    if style == "proper":
        if text[-1] not in ".!?)" and not re.search(r"[:;]-?[)(DPp]$|<3$|xD$|o7$", text):
            text += "."
    elif style in ("natural", "casual"):
        # Chat convention: no full stop on the last sentence; keep ? and !
        if text.endswith(".") and not text.endswith("..."):
            text = text[:-1]
        if style == "casual":
            text = _decapitalize_first(text, protected)
            text = re.sub(r"([.!?]\s+)([A-Z][a-z']*)",
                          lambda m: m.group(1) + _decapitalize_first(m.group(2), protected), text)
    return text


def wow_safe(text: str) -> str:
    # "|" starts WoW escape sequences (colours, links) - never send a bare one.
    text = text.replace("|", "/")
    # Strip control characters and emoji WoW's chat font can't draw.
    text = "".join(ch for ch in text if ch.isprintable() and ord(ch) < 0x2000 or ch in "\u2019")
    return text.strip()


def split_message(text: str, room: int) -> list[str]:
    if len(text) <= room:
        return [text]
    out, cur = [], ""
    for sent in _sentences(text):
        while len(sent) > room:
            cut = sent.rfind(" ", 0, room)
            cut = room if cut <= 0 else cut
            if cur:
                out.append(cur)
                cur = ""
            out.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if len(cur) + len(sent) + 1 <= room:
            cur = f"{cur} {sent}".strip()
        else:
            out.append(cur)
            cur = sent
    if cur:
        out.append(cur)
    return [p for p in out if p]


# --------------------------------------------------------------------- pipeline
class Formatter:
    def __init__(self, lexicon: Lexicon, cfg):
        self.lexicon = lexicon
        self.cfg = cfg

    def process(self, raw: str) -> ChatLine:
        cfg = self.cfg
        self.lexicon.shorthand = cfg.shorthand
        text = tidy_spacing(_collapse_dotted_acronyms(raw))
        if cfg.remove_fillers:
            text = remove_fillers(text)
        line = parse_commands(tidy_spacing(text), cfg.channel_numbers, cfg.voice_channels)
        if line.cancelled:
            return line
        body = line.text
        if cfg.spoken_punctuation:
            body = apply_spoken_punctuation(body)
        body = numbers_to_digits(tidy_spacing(body))
        body, reps = self.lexicon.correct(body)
        line.replacements = reps
        protected = {w.lower() for r in reps for w in r.new.split()}
        if line.slash:
            body = body.rstrip(".")
        elif line.prefix.startswith("/e "):
            # Emotes read "Name waves" - keep them lowercase, no trailing stop.
            body = _decapitalize_first(tidy_spacing(body), protected).rstrip(".")
        else:
            body = apply_style(tidy_spacing(body), cfg.style, protected)
        line.text = wow_safe(body)
        return line
