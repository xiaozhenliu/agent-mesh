#!/usr/bin/env python3
"""Render installer prompts from the user-facing Markdown; no network or config reads."""
import argparse
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "zh-CN")


def render_prompt(template, language="en", *, team_name=None, mac_user=None, mac_home=None, repo_url=None):
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language: {language}; choose en or zh-CN")
    if template not in ("routine", "cloud-setup"):
        raise ValueError(f"unknown prompt: {template}")
    suffix = "" if language == "en" else f".{language}"
    source = ROOT / "prompts" / f"{template}{suffix}.md"
    blocks = re.findall(r"^```(?:text)?\n(.*?)^```\s*$", source.read_text(encoding="utf-8"), re.M | re.S)
    if len(blocks) != 1:
        raise ValueError(f"{source}: expected exactly one prompt code block")
    # mac_user is retained for offline previews made with the earlier CLI.
    if mac_home is None and mac_user is not None:
        mac_home = f"/Users/{mac_user}"
    values = {"<team-name>": team_name, "<mac-user>": mac_user, "<mac-home>": mac_home,
              "<repo-url>": shlex.quote(repo_url) if repo_url is not None else None}
    # One pass: replacement values remain literal, including &, backslashes, and placeholder-like text.
    return re.sub(r"<team-name>|<mac-user>|<mac-home>|<repo-url>",
                  lambda match: values[match[0]] if values[match[0]] is not None else match[0], blocks[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", choices=("routine", "cloud-setup"))
    parser.add_argument("--lang", choices=LANGUAGES, default="en")
    parser.add_argument("--team-name")
    parser.add_argument("--mac-user")
    parser.add_argument("--mac-home", help="actual absolute home directory on the user's Mac")
    parser.add_argument("--repo-url")
    args = parser.parse_args()
    try:
        print(render_prompt(args.template, args.lang, team_name=args.team_name,
                            mac_user=args.mac_user, mac_home=args.mac_home, repo_url=args.repo_url), end="")
    except (OSError, ValueError) as error:
        parser.exit(1, f"prompt: {error}\n")


if __name__ == "__main__":
    main()
