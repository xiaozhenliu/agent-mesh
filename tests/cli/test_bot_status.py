"""scripts/bot_status.sh reads one Bot line of deploy_state.py status by key name, in any order."""
import subprocess
import unittest

from render_prompt import ROOT

HARNESS = r'''
. "$1/scripts/bot_status.sh"
while IFS='|' read -r keys line; do
  read -r -a w <<< "$line"
  if bot_status_read "${w[@]}"; then
    read -r -a k <<< "$keys"
    if bot_status_has "${k[@]}"; then has=yes; else has=no; fi
    printf '%s\n' "$BS_NAME|$BS_KIND|$BS_STAGE|$BS_WEBHOOK|$BS_RELAY|$BS_PROMPT|$BS_CLOUD|$BS_REMOVING|$BS_ADDRESS|$has"
  else
    echo refused
  fi
done
'''
LINE = ("bot alpha kind legacy stage relay webhook ok relay unchanged prompt pending cloud confirmed removing - "
        "record yes address nova")
VALUES = ("alpha", "legacy", "relay", "ok", "unchanged", "pending", "confirmed", "-", "nova")
ALL = "kind stage webhook relay prompt cloud removing record address"


def read_as(*values, has):
    return "|".join(values + ("",) * (len(VALUES) - len(values)) + (has,))


class BotStatus(unittest.TestCase):
    def read(self, *cases):
        text = "".join(f"{keys}|{line}\n" for keys, line in cases)
        result = subprocess.run(["bash", "-c", HARNESS, "harness", str(ROOT)], input=text, capture_output=True,
                                text=True, timeout=30)
        self.assertEqual((result.returncode, result.stderr), (0, ""))
        return result.stdout.splitlines()

    def test_every_key_is_read_by_name_and_unknown_keys_are_skipped(self):
        words = LINE.split()
        pairs = [words[i:i + 2] for i in range(2, len(words), 2)][::-1]
        reordered = " ".join(words[:2] + ["*", "value", "kin?", "added"] + [w for p in pairs for w in p]
                             + ["future", "a", "future", "b", "*", "b"])
        self.assertEqual(self.read((ALL, LINE), (ALL, reordered), ("", "bot alpha kind stage stage relay"),
                                   ("relay", "bot alpha stage relay")),
                         [read_as(*VALUES, has="yes"), read_as(*VALUES, has="yes"),
                          read_as("alpha", "stage", "relay", has="yes"), read_as("alpha", "", "relay", has="no")])

    def test_a_missing_key_is_empty_and_fails_only_the_check_that_needs_it(self):
        line = LINE.replace(" address nova", "")
        self.assertEqual(self.read((ALL, line), (ALL.replace(" address", ""), line), ("address kind", line),
                                   ("kind", "bot beta"), ("", "bot beta")),
                         [read_as(*VALUES[:-1], has="no"), read_as(*VALUES[:-1], has="yes"),
                          read_as(*VALUES[:-1], has="no"), read_as("beta", has="no"), read_as("beta", has="yes")])

    def test_each_read_starts_empty(self):
        self.assertEqual(self.read(("", LINE), ("kind", "bot beta kind added"), ("record", "bot gamma")),
                         [read_as(*VALUES, has="yes"), read_as("beta", "added", has="yes"), read_as("gamma", has="no")])

    def test_a_repeated_key_a_key_without_a_value_or_another_line_is_refused(self):
        lines = [LINE + " removing stopped", LINE + " record no", LINE + " removing -", LINE + " stage relay",
                 LINE + " relay unchanged", LINE + " record yes", LINE + " future", LINE.replace("bot ", "bots ", 1),
                 "bot", "", "alpha kind legacy"]
        self.assertEqual(self.read(*(("", line) for line in lines)), ["refused"] * len(lines))


if __name__ == "__main__":
    unittest.main()
