"""Finding a detected string again in text, however the text breaks it.

A reviewer reads each mention and decides. A copy nobody reviews, such as a
redacted PDF, needs every repeat of a detection found, and a repeat is written
the way the page broke it: over a line ("Hildegard\\nZwyssig"), hyphenated at
a line end ("Basel-" and "Landschaft", or docling's "BaselLandschaft"),
letter-spaced ("Hi ldegard"), with or without its accents ("Strassencafés"),
with a ligature ("Sta\\ufb00elberg") or an underscore for a space
("GKZ_Entwurf.doc").
"""

import re
import unicodedata

import stopwordsiso

#: Languages the documents come in, for the stopword lists (stopwords-iso).
LANGUAGES = ["de", "fr", "it", "en"]

#: Every dash a document writes a hyphen as, folded to the plain one.
_DASHES = str.maketrans(dict.fromkeys("\u00ad\u2010\u2011\u2012\u2013\u2014\u2212", "-"))


def find(needle: str, text: str) -> list[tuple[int, int]]:
    """Where a string stands on its own in text, as character offsets of ``text``.

    Both are compared folded: compatibility forms to plain letters, accents
    dropped, case folded (so "ß" is "ss"), every dash written as a plain
    hyphen. Offsets are mapped back to the text as it was.
    """
    folded, origin = _folded(text)
    pattern = _standalone(_folded(needle)[0])
    if pattern is None:
        return []
    return [(origin[match.start()], origin[match.end() - 1] + 1) for match in pattern.finditer(folded)]


def is_scrap(text: str) -> bool:
    """Whether a detection is a scrap no entity type can be.

    Nothing but lowercase words like "für", "die", "the", or no run of two
    letters or digits in it ("[d]", "e e  e"). The model offers such scraps
    now and then when it reads OCR of a plan or a table.

    Only lowercase: a written mention carries its capital, and the stopword
    lists hold "ag" and "bs" as words. Words are what the text writes as one,
    so "stata@bs.ch" is a word of its own and not the three stopwords it is
    spelled with.
    """
    folded = _folded(text)[0]
    if not any(len(run) >= 2 for run in re.findall(r"[^\W_]+", folded)):
        return True
    return text.islower() and all(_bare(word) in _STOPWORDS for word in folded.split())


def _bare(word: str) -> str:
    """A word without the punctuation a sentence sets around it."""
    return word.strip(".,;:!?()[]{}<>«»\"'")


def _standalone(needle: str) -> re.Pattern[str] | None:
    """The needle standing on its own, however its words are broken.

    Not \\b: a hyphen is a word boundary to it, so "Basel" would be taken out
    of "Basel-Stadt", and an underscore is not, so "GKZ" would stay in
    "GKZ_Entwurf.doc", where every reader sees it. Any whitespace between
    words matches any other.
    """
    words = [word for word in (_word(word) for word in needle.split()) if word]
    if not words:
        return None
    return re.compile(rf"(?<![^\W_])(?<!['\-]){r'\s+'.join(words)}(?![^\W_])(?!['\-])", re.IGNORECASE)


def _word(word: str) -> str:
    """A word, with whitespace and hyphens allowed between two of its letters.

    Only between letters: "2.45" is not in "5.2.4 5.2.5". A hyphen between
    anything else ("2026-0417") may be there or not, but nothing else may.
    """
    pattern, previous, hyphen = "", "", False
    for char in word:
        if char == "-":
            hyphen = True
            continue
        if previous.isalpha() and char.isalpha():
            pattern += r"[\s\-]*"
        elif hyphen and previous:
            pattern += "-?"
        pattern += re.escape(char)
        previous, hyphen = char, False
    return pattern


def _folded(text: str) -> tuple[str, list[int]]:
    """``text`` folded, and for each folded character the original's offset."""
    pieces, origin = [], []
    for index, char in enumerate(text):
        decomposed = unicodedata.normalize("NFKD", char).casefold()
        plain = "".join(part for part in decomposed if not unicodedata.combining(part)).translate(_DASHES)
        pieces.append(plain)
        origin += [index] * len(plain)
    return "".join(pieces), origin


_STOPWORDS = frozenset(_folded(word)[0] for word in stopwordsiso.stopwords(LANGUAGES))
