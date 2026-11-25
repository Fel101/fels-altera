"""
Altera — Hard content rules, enforced in code.

These rules hold at every adult-mode level and do not depend on the chat model. An uncensored ("heretic") model refuses
nothing on its own, so what must always be true is checked here instead of being left to the prompt:

  * avatars are adults (MIN_AVATAR_AGE, the legal floor) and nothing describes a minor or a childlike character;
  * nothing depicts a real, identifiable person;
  * nothing is non-consensual.

The word lists are heuristics. They catch the obvious phrasings, not every phrasing. The second layer is the image age
check (core/age_check.py), which looks at the finished picture. Add-ons cannot loosen either layer.
"""
import re

MIN_AVATAR_AGE = 18        # the legal floor. Not configurable: there is no setting that lowers it
MIN_APPARENT_AGE = 18      # what the image age check must estimate before a picture is delivered. It is an ESTIMATE by a vision
                           # model and can be several years off either way: a second layer behind the text rules, not a guarantee

ADDON_RULES = (
    "Rules that never change, whatever the user asks: you are an adult; never sexualise anyone under 18 or anyone who is "
    "described or implied to be young, childlike or at school; never involve a real person or anyone the user knows; "
    "nothing non-consensual, coerced or violent. Decline in your own voice and change direction."
)

# Always added to an image's negative prompt.
YOUTH_NEGATIVE = "child, childlike, minor, underage, teenager, teen, youthful, baby face, schoolgirl, school uniform"

_NUM_WORDS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen"
_MINOR_DIGITS = r"(?:1[0-7]|[1-9])"
_MINOR_NUM = rf"(?:{_NUM_WORDS}|1[0-7]|[1-9])"
_UNITS = (r"min(?:ute)?s?|hours?|hrs?|days?|weeks?|months?|miles?|km|kg|lbs?|pounds?|feet|foot|ft|inch(?:es)?|cm|mm|"
          r"percent|%|dollars?|bucks?|euros?|times?|items?|people|more")

# Clear signals of a minor. Used on chat messages, where "kid" or "child" often just means the user's own family.
_YOUTH_STRONG = [
    r"\b(?:teen|teens|teenage|teenaged|teenager|teenagers|preteen|pre-teen|tween|tweens)\b",
    r"\b(?:underage|under-age)\b",
    r"\b(?:schoolgirl|schoolboy|school\s?girl|school\s?boy|high\s?school|middle\s?school|junior\s?high|grade\s?school|"
    r"elementary|school\s?uniform)s?\b",
    r"\b(?:loli|lolita|shota|jailbait|barely\s+legal)\b",
    r"\b(?:little|young)\s+(?:girl|boy)s?\b",
    r"\bunder\s*(?:the\s+age\s+of\s+)?(?:18|eighteen)\b",
    r"\b(?:not\s+yet|almost|nearly)\s+(?:18|eighteen)\b",
    rf"\b{_MINOR_NUM}\s*-?\s*(?:years?|yrs?)\s*-?\s*old\b",
    rf"\b{_MINOR_NUM}\s*-?\s*(?:yo|y/o)\b",
    rf"\b(?:aged?|age)\s*(?:of\s+)?{_MINOR_DIGITS}\b(?!\s*(?:{_UNITS}))",
    rf"\b(?:she|he|they|i|you)(?:'s|'m|'re|\s+(?:is|was|am|are|were))\s+{_MINOR_DIGITS}\b(?!\s*(?:{_UNITS}|and\b)|\s*[-/.:]\d)",
]
# Everything above plus the family words. Used on picture descriptions and avatar text, where they have no innocent use.
_YOUTH_ALL = _YOUTH_STRONG + [
    r"\b(?:child|children|childlike|childish|kid|kids|toddler|infant|juvenile|adolescent|minors)\b",
]
_YOUTH_STRONG_RE = re.compile("|".join(_YOUTH_STRONG), re.I)
_YOUTH_ALL_RE = re.compile("|".join(_YOUTH_ALL), re.I)

_SEXUAL_RE = re.compile(
    r"\b(?:sex|sexy|sexual\w*|nude|naked|topless|bottomless|erotic\w*|horny|porn\w*|nsfw|fuck\w*|cock|dick|pussy|tits?|"
    r"boobs?|breasts?|nipples?|cum|cumming|orgasm\w*|blowjob|masturbat\w*|lingerie|undress\w*|strip(?:ping|tease)?|"
    r"fetish|kinky|naughty|seduc\w*|aroused|arousal)\b", re.I)
_CORE_PICTURE_RE = re.compile(
    r"\b(?:swimsuits?|swimwear|bikinis?|bathing\s+suits?|trunks|shirtless|underwear|panties|bras?|bralettes?|thongs?|"
    r"negligees?|babydolls?|corsets?|stockings?|garters?|see-through|sheer|cleavage|bathtub|in\s+the\s+shower|"
    r"towel\s+only|wet\s+t-shirt|sultry|areolas?|genitals?|vagina|penis|erect|intercourse|undressed|no\s+clothes|"
    r"nothing\s+on|without\s+clothes|spread\s+legs)\b", re.I)


def core_picture_term(text):
    match = _CORE_PICTURE_RE.search(text or "") or _SEXUAL_RE.search(text or "")
    return match.group(0) if match else None

# Real, identifiable people: celebrity and likeness phrasing, and "looks like <Name>".
_REAL_PERSON_RE = re.compile(
    r"\b(?:celebrit\w*|public\s+figure|deep-?fakes?|face[- ]?swap\w*|look-?alikes?|likeness|"
    r"famous\s+(?:actress|actor|singer|model|person|people))\b", re.I)
_REAL_PERSON_NAMED_RE = re.compile(          # the verb is case-insensitive, the name after it must be capitalised
    r"(?i:\b(?:looks?\s+like|looking\s+like|resembl(?:es|ing)|impersonat(?:es|ing)|version\s+of)\s+(?:the\s+)?)[A-Z][a-z]+")

_NON_CONSENT_RE = re.compile(
    r"\b(?:rap(?:e|ed|es|ing|ist)|non-?consensual|without\s+(?:her|his|their|my|your)\s+consent|"
    r"against\s+(?:her|his|their|my|your)\s+will|forced\s+(?:to|into)|unconscious|drugged|molest\w*|coerc\w*|"
    r"blackmail\w*|held\s+down|sexually\s+assault\w*|passed\s+out)\b", re.I)


def has_minor_signal(text: str, strong_only: bool = False) -> bool:
    """True if the text describes or hints at a minor or a childlike character."""
    pattern = _YOUTH_STRONG_RE if strong_only else _YOUTH_ALL_RE
    return bool(pattern.search(text or ""))


def blocks_message(text: str) -> str | None:
    """Chat tripwire: a sexual message that also points at a minor never reaches the model. Returns the reason or None."""
    if _YOUTH_STRONG_RE.search(text or "") and _SEXUAL_RE.search(text):
        return "minor_sexual"
    return None


def hard_problem(text: str) -> str | None:
    """Why a picture description or avatar text can never be used, or None. Applies at every adult-mode level."""
    text = text or ""
    if _YOUTH_ALL_RE.search(text):
        return "It mentions a minor or a childlike detail, which is never allowed."
    if _REAL_PERSON_RE.search(text) or _REAL_PERSON_NAMED_RE.search(text):
        return "It refers to a real person's likeness, which is never allowed."
    if _NON_CONSENT_RE.search(text):
        return "It describes something non-consensual, which is never allowed."
    return None


# ---- kids mode (core/kids.py) ----------------------------------------------------------------------------------------
# Topics a pal in kids mode does not talk about. Checked on the child's message (before the model sees it) and on the
# model's reply (before the child sees it). Word lists, like the rules above: they catch the obvious phrasings, not all.
_KIDS_TOPICS = (
    ("self_harm", re.compile(
        r"\b(?:suicid\w*|self[- ]?harm\w*|kill(?:ing)?\s+my\s?self|hurt(?:ing)?\s+my\s?self|cut(?:ting)?\s+my\s?self|"
        r"want(?:ed)?\s+to\s+die|wish\s+i\s+(?:was|were)\s+dead|end\s+(?:it\s+all|my\s+life)|"
        r"(?:starv|not\s+eat)\w*\s+my\s?self)\b", re.I)),
    # Not _SEXUAL_RE: that list has words a child uses innocently ("naughty", "comic strip", "blue tits")
    ("sexual", re.compile(
        r"\b(?:sex|sexy|sexual\w*|nude|nudes|naked|porn\w*|nsfw|horny|erotic\w*|orgasm\w*|masturbat\w*|blowjob|fuck\w*|"
        r"pussy|boobs?|nipples?|lingerie|undress\w*|striptease|fetish|kinky|aroused|arousal)\b", re.I)),
    # Romance aimed at the pal (or by it), not "my sister has a boyfriend"
    ("sexual", re.compile(
        r"\b(?:(?:be|am|i'm|i\s+am)\s+(?:my|your)\s+(?:boyfriend|girlfriend|lover|crush)|date\s+(?:me|you)|"
        r"kiss(?:ing)?\s+(?:me|you)|in\s+love\s+with\s+(?:you|me)|make\s+out|making\s+out|hook\s?up)\b", re.I)),
    ("drugs", re.compile(
        r"\b(?:drugs?|weed|marijuana|cannabis|cocaine|heroin|meth|mdma|ecstasy|lsd|vap(?:e|es|ing)|cigarettes?|"
        r"alcohol|vodka|whiske?y|tequila|get(?:ting)?\s+(?:drunk|high|wasted|stoned))\b", re.I)),
    ("violence", re.compile(
        r"\b(?:gore|gory|behead\w*|tortur\w*|massacre\w*|murder\w*|dismember\w*|school\s+shoot\w*|mass\s+shoot\w*|"
        r"how\s+to\s+(?:make|build)\s+(?:a\s+)?(?:bomb|gun|weapon|explosive)s?|kill\s+(?:someone|somebody|people|him|her|them))\b",
        re.I)),
    ("gambling", re.compile(r"\b(?:gambl\w*|casinos?|sports\s+betting|bet(?:ting)?\s+(?:money|cash))\b", re.I)),
    ("private", re.compile(
        r"\b(?:my\s+(?:home\s+)?address\s+is|i\s+live\s+(?:at|on)\s+\d|my\s+(?:phone|cell|mobile)\s+(?:number\s+)?is|"
        r"what(?:'s|\s+is)\s+your\s+(?:home\s+)?(?:address|phone\s+number)|meet\s+(?:me\s+|up\s+)?in\s+(?:person|real\s+life)|"
        r"my\s+password\s+is|send\s+(?:me\s+)?(?:a\s+)?(?:photo|pic|picture|selfie)s?\s+of\s+you)\b",
        re.I)),
)


# The model asking a child for personal details (checked on replies only: a child asking an AI "where do you live?" is fine)
_KIDS_ASKS_RE = re.compile(
    r"\b(?:what(?:'s|\s+is)\s+your\s+(?:full\s+name|address|phone\s+number|password|school(?:'s\s+name)?)|"
    r"where\s+do\s+you\s+live|which\s+school\s+do\s+you\s+go\s+to|send\s+me\s+(?:a\s+)?(?:photo|pic|picture|selfie)|"
    r"(?:don'?t|do\s+not)\s+tell\s+your\s+(?:parents|mum|mom|dad)|our\s+(?:little\s+)?secret)\b", re.I)


def kids_reply_problem(text: str) -> str | None:
    """A restricted topic in the model's reply, or the model asking the child for personal details or secrecy; or None."""
    if _KIDS_ASKS_RE.search(text or ""):
        return "private"
    return kids_topic(text)


def kids_topic(text: str) -> str | None:
    """Which kids-mode topic the text touches ('self_harm', 'sexual', 'drugs', 'violence', 'gambling', 'private'), or None.
    Self-harm comes first: it gets its own reply."""
    text = text or ""
    for topic, pattern in _KIDS_TOPICS:
        if pattern.search(text):
            return topic
    return None


def age_problem(age) -> str | None:
    """Why an avatar's age is not acceptable, or None."""
    try:
        age = int(age)
    except (TypeError, ValueError):
        return f"Avatars need a stated age of {MIN_AVATAR_AGE} or older."
    if age < MIN_AVATAR_AGE:
        return f"Avatars must be {MIN_AVATAR_AGE} or older (this one is {age})."
    return None


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def card_problem(card: dict) -> str | None:
    """Why an avatar card is not acceptable (age, or any text describing a minor or a real person), or None."""
    problem = age_problem(card.get("age"))
    if problem:
        return problem
    for text in _strings({k: v for k, v in card.items() if k != "age"}):
        found = hard_problem(text)
        if found:
            return f"The avatar's description is not allowed: {found[0].lower()}{found[1:]}"
    return None
