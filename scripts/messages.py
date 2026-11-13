import os
import sys
import unicodedata
from pathlib import Path

DIR = Path(__file__).resolve().parent / "messages"
LANGUAGES = ("en", "zh-CN")
ESCAPES = {"n": "\n", "t": "\t", "\\": "\\"}
_cache = {}


def decode(text):
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in ESCAPES:
            out.append(ESCAPES[text[i + 1]])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def catalog(lang):
    if lang not in _cache:
        table = {}
        for path in sorted((DIR / lang).glob("*.tsv")):
            for line in path.read_text(encoding="utf-8").splitlines():
                key, sep, text = line.partition("\t")
                if sep and key not in table:
                    table[key] = text
        _cache[lang] = table
    return _cache[lang]


def language(lang=None):
    lang = lang or os.environ.get("AGENT_MESH_LANG") or "en"
    return lang if lang in LANGUAGES else "en"


def t(key, *args, lang=None):
    text = catalog(language(lang)).get(key)
    if text is None:
        text = catalog("en").get(key, key)
    text = decode(text)
    return text % args if args else text.replace("%%", "%")


def width(text):
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def pad(text, size):
    return text + " " * max(0, size - width(text))


def row(args):
    out = []
    while len(args) > 1:
        out.append(pad(args[1], int(args[0])))
        args = args[2:]
    return ("  " + " ".join(out + list(args))).rstrip()


if __name__ == "__main__" and sys.argv[1:2] == ["row"]:
    sys.stdout.reconfigure(encoding="utf-8")
    print(row(sys.argv[2:]))
