#!/usr/bin/env python3
"""Render installer prompts from the user-facing Markdown; no network or config reads."""
import argparse
import hashlib
import re
import shlex
from pathlib import Path

from relay_names import problem as relay_name_problem

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "zh-CN")
TEMPLATES = ("routine", "cloud-setup", "cloud-uninstall")
CLOUD_SCRIPT = ROOT / "box" / "rn-cloud-uninstall.sh"
HEREDOC_END = "AGENT_MESH_UNINSTALL_EOF"
TEAM = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
UNINSTALL_KEYS = ("<team-name>", "<mac-machine>", "<relay-name>", "<level>", "<mode>", "<challenge>")


def uninstall_values(team_name, mac_machine, relay_name, level, mode, challenge):
    for label, value in (("team name", team_name), ("Mac machine name", mac_machine)):
        if value is not None and not (TEAM.fullmatch(value) and len(value) <= 40):
            raise ValueError(f"invalid {label}: {value[:60]!r}")
    if level is not None and level not in ("identity", "all"):
        raise ValueError(f"invalid level: {level}; choose identity or all")
    if mode is not None and mode not in ("normal", "residual"):
        raise ValueError(f"invalid mode: {mode}; choose normal or residual")
    if challenge is not None and not re.fullmatch(r"[0-9a-f]{16}", challenge):
        raise ValueError("the challenge must be 16 lowercase hex digits")
    return dict(zip(UNINSTALL_KEYS, (team_name, mac_machine, relay_name, level, mode, challenge)))


def substitute(text, values):
    pattern = "|".join(re.escape(key) for key in values)
    return re.sub(pattern, lambda match: values[match[0]] if values[match[0]] is not None else match[0], text)


def cloud_uninstall_script(team_name=None, mac_machine=None, relay_name=None, level=None, mode=None,
                           challenge=None):
    if relay_name is not None and relay_name_problem(relay_name):
        raise ValueError(relay_name_problem(relay_name))
    values = uninstall_values(team_name, mac_machine, relay_name, level, mode, challenge)
    text = substitute(CLOUD_SCRIPT.read_text(encoding="utf-8"), values).rstrip("\n") + "\n"
    if HEREDOC_END in text:
        raise ValueError(f"{CLOUD_SCRIPT}: contains {HEREDOC_END}")
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def render_prompt(template, language="en", *, team_name=None, mac_user=None, mac_home=None, repo_url=None,
                  relay_name=None, mac_machine=None, level=None, mode=None, challenge=None):
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language: {language}; choose en or zh-CN")
    if template not in TEMPLATES:
        raise ValueError(f"unknown prompt: {template}")
    if relay_name is not None and relay_name_problem(relay_name):
        raise ValueError(relay_name_problem(relay_name))
    suffix = "" if language == "en" else f".{language}"
    source = ROOT / "prompts" / f"{template}{suffix}.md"
    blocks = re.findall(r"^```(?:text)?\n(.*?)^```\s*$", source.read_text(encoding="utf-8"), re.M | re.S)
    if len(blocks) != 1:
        raise ValueError(f"{source}: expected exactly one prompt code block")
    if template == "cloud-uninstall":
        script, digest = cloud_uninstall_script(team_name, mac_machine, relay_name, level, mode, challenge)
        values = uninstall_values(team_name, mac_machine, relay_name, level, mode, challenge)
        values.update({"<script-sha256>": digest, "<cloud-script>": script.rstrip("\n")})
        return substitute(blocks[0], values)
    # mac_user is retained for offline previews made with the earlier CLI.
    if mac_home is None and mac_user is not None:
        mac_home = f"/Users/{mac_user}"
    values = {"<team-name>": team_name, "<mac-user>": mac_user, "<mac-home>": mac_home,
              "<repo-url>": shlex.quote(repo_url) if repo_url is not None else None, "<relay-name>": relay_name}
    # One pass: replacement values remain literal, including &, backslashes, and placeholder-like text.
    return re.sub(r"<team-name>|<mac-user>|<mac-home>|<repo-url>|<relay-name>",
                  lambda match: values[match[0]] if values[match[0]] is not None else match[0], blocks[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", choices=TEMPLATES)
    parser.add_argument("--lang", choices=LANGUAGES, default="en")
    parser.add_argument("--team-name")
    parser.add_argument("--mac-user")
    parser.add_argument("--mac-home", help="actual absolute home directory on the user's Mac")
    parser.add_argument("--repo-url")
    parser.add_argument("--relay-name", help="Bot address name chosen with install.sh --relay-name")
    parser.add_argument("--mac-machine", help="cloud-uninstall: the Mac's machine name in the team")
    parser.add_argument("--level", choices=("identity", "all"), help="cloud-uninstall: uninstall level")
    parser.add_argument("--mode", choices=("normal", "residual"), help="cloud-uninstall: normal or residual")
    parser.add_argument("--challenge", help="cloud-uninstall: 16 hex digits, new for each uninstall run")
    args = parser.parse_args()
    try:
        print(render_prompt(args.template, args.lang, team_name=args.team_name,
                            mac_user=args.mac_user, mac_home=args.mac_home, repo_url=args.repo_url,
                            relay_name=args.relay_name, mac_machine=args.mac_machine, level=args.level,
                            mode=args.mode, challenge=args.challenge), end="")
    except (OSError, ValueError) as error:
        parser.exit(1, f"prompt: {error}\n")


if __name__ == "__main__":
    main()
