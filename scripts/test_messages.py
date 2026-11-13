"""Terminal text catalogs: bash (scripts/msg.sh) and Python (scripts/messages.py) print the same bytes."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import messages

ROOT = Path(__file__).resolve().parents[1]
ARGS = ("v1.2.3", "100% done", "a\\nb", "-x", "名字")


def keys(lang):
    return sorted(messages.catalog(lang))


def bash_render(lang, cases):
    script = ('. "$1"\nwhile IFS= read -r line; do\n  IFS=$\'\\t\' read -r -a parts <<< "$line"\n'
              '  tq "${parts[@]}"\n  printf \'\\0\'\ndone < "$2"\n')
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".tsv", delete=False) as stream:
        for case in cases:
            stream.write("\t".join(case) + "\n")
    try:
        env = dict(os.environ, AGENT_MESH_LANG=lang, LC_ALL="en_US.UTF-8")
        out = subprocess.run(["/bin/bash", "-c", "set -u\n" + script, "bash", str(ROOT / "scripts/msg.sh"),
                              stream.name], env=env, capture_output=True, timeout=300, check=True).stdout
    finally:
        os.unlink(stream.name)
    return out.decode("utf-8").split("\0")[:-1]


class Catalogs(unittest.TestCase):
    def test_bash_and_python_print_the_same_text_for_every_key(self):
        for lang in messages.LANGUAGES:
            cases = []
            for key in keys(lang):
                count = messages.catalog(lang)[key].replace("%%", "").count("%s")
                cases.append((key,) + tuple(ARGS[i % len(ARGS)] for i in range(count)))
            with self.subTest(lang=lang):
                got = bash_render(lang, cases)
                self.assertEqual(len(got), len(cases))
                for case, text in zip(cases, got):
                    self.assertEqual(text, messages.t(case[0], *case[1:], lang=lang), case)

    def test_both_languages_have_the_same_keys(self):
        self.assertEqual(keys("en"), keys("zh-CN"))

    def test_unknown_keys_and_languages_fall_back(self):
        self.assertEqual(messages.t("no.such_key"), "no.such_key")
        key = keys("en")[0]
        self.assertEqual(messages.t(key, *ARGS[:messages.catalog("en")[key].count("%s")], lang="fr"),
                         messages.t(key, *ARGS[:messages.catalog("en")[key].count("%s")], lang="en"))
        got = bash_render("fr", [("no.such_key",)])
        self.assertEqual(got, ["no.such_key"])

    def test_escapes_decode_once(self):
        self.assertEqual(messages.decode("a\\nb\\tc\\\\n\\x"), "a\nb\tc\\n\\x")


if __name__ == "__main__":
    unittest.main()
