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
TEMPLATES = ("routine", "cloud-setup", "cloud-uninstall", "cloud-add-bot")
CLOUD_SCRIPT = ROOT / "box" / "rn-cloud-uninstall.sh"
CLOUD_FILES = ("box/rn-cloud.sh", "box/rn-fetch.py", "box/rn-cloud-setup.sh", "box/rn-engine.py",
               "box/rn-cloud-add-bot.sh")
HEREDOC_END = "AGENT_MESH_UNINSTALL_EOF"
TEAM = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
UNINSTALL_KEYS = ("<team-name>", "<mac-machine>", "<relay-name>", "<level>", "<mode>", "<challenge>")
CLOUD_BOX = "/workspace/agent-mesh/box/"
THREADS = "/workspace/agent-mesh/threads/"
RENDER_KEYS = re.compile(r"<(?:team-name|relay-name|team-hash|mac-user|mac-home|repo-url|release-tag|release-commit|"
                         r"cloud-checksums)>")


def check_team(label, value):
    if value is not None and not (TEAM.fullmatch(value) and len(value) <= 40):
        raise ValueError(f"invalid {label}: {value[:60]!r}")


def check_team_hash(team_hash):
    if team_hash is not None and not re.fullmatch(r"[0-9a-f]{16}", team_hash):
        raise ValueError("the team hash must be 16 lowercase hex digits")


def uninstall_values(team_name, mac_machine, relay_name, level, mode, challenge):
    check_team("team name", team_name)
    check_team("Mac machine name", mac_machine)
    if level is not None and level not in ("bot", "identity", "all"):
        raise ValueError(f"invalid level: {level}; choose bot, identity or all")
    if mode is not None and mode not in ("normal", "residual"):
        raise ValueError(f"invalid mode: {mode}; choose normal or residual")
    if level == "bot" and mode is not None and mode != "normal":
        raise ValueError("level bot needs mode normal")
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


def release_values(release_tag, release_commit, cloud_checksums):
    if release_tag is not None and not re.fullmatch(r"v\d+\.\d+\.\d+", release_tag):
        raise ValueError(f"invalid release tag: {release_tag[:60]!r}")
    if release_commit is not None and not re.fullmatch(r"[0-9a-f]{40}", release_commit):
        raise ValueError("the release commit must be 40 lowercase hex digits")
    if cloud_checksums is not None:
        lines = cloud_checksums.strip("\n").split("\n")
        if (not all(re.fullmatch(r"[0-9a-f]{64}  box/[A-Za-z0-9._-]+", line) for line in lines)
                or sorted(line[66:] for line in lines) != sorted(CLOUD_FILES)):
            raise ValueError("the cloud checksums must be one line of '<sha256>  <file>' for each of "
                             + ", ".join(CLOUD_FILES))
        cloud_checksums = "\n".join(lines)
    return {"<release-tag>": release_tag, "<release-commit>": release_commit, "<cloud-checksums>": cloud_checksums}


def prompt_block(template, language):
    suffix = "" if language == "en" else f".{language}"
    source = ROOT / "prompts" / f"{template}{suffix}.md"
    blocks = re.findall(r"^```(?:text)?\n(.*?)^```\s*$", source.read_text(encoding="utf-8"), re.M | re.S)
    if len(blocks) != 1:
        raise ValueError(f"{source}: expected exactly one prompt code block")
    return blocks[0]


def add_bot_values(relay_name, team_name, team_hash):
    check_team("team name", team_name)
    check_team_hash(team_hash)
    return {"<relay-name>": relay_name, "<team-name>": team_name, "<team-hash>": team_hash}


def selector_routine(text, language, name, team_name, team_hash):
    command = f"RN_BOT={name} {CLOUD_BOX}rn-cloud.sh"
    text = text.replace(f"{CLOUD_BOX}rn-cloud.sh", command)
    text = re.sub(r"(?<!/)rn-cloud\.sh send", lambda _: f"{command} send", text)
    text = text.replace(THREADS, f"/workspace/agent-mesh/bots/{name}/threads/")
    lines = text.split("\n")
    first = [i for i, line in enumerate(lines) if line.startswith("1. ")]
    if len(first) != 1:
        raise ValueError(f"routine ({language}): expected exactly one step 1")
    step0 = substitute(prompt_block("cloud-add-bot", language), add_bot_values(name, team_name, team_hash))
    lines[first[0]:first[0]] = step0.rstrip("\n").split("\n")
    text = "\n".join(lines)
    register = f"rn-cloud-add-bot.sh '{name}' '{team_name}' '{team_hash}'"
    check = f"rn-cloud-add-bot.sh --check '{name}' '<req>'"
    problems = []
    if any(not text.endswith(f"RN_BOT={name} {CLOUD_BOX}", 0, m.start()) for m in re.finditer(r"rn-cloud\.sh", text)):
        problems.append(f"an rn-cloud.sh command without RN_BOT={name}")
    if THREADS in text:
        problems.append("the first Bot's threads path")
    if sum(line.startswith("0. ") for line in text.split("\n")) != 1 or text.count(register) != 1 \
            or text.count(check) != 1:
        problems.append("step 0 not exactly once")
    if f"{team_name}/{name}-cloud" not in text:
        problems.append("the team name or Bot address name not filled in")
    if RENDER_KEYS.search(text):
        problems.append(f"an unfilled placeholder {RENDER_KEYS.search(text)[0]}")
    if problems:
        raise ValueError(f"routine ({language}) for Bot {name}: " + "; ".join(problems))
    return text


def selector_uninstall(text, language, name):
    command = f"RN_BOT={name} {CLOUD_BOX}rn-cloud.sh"
    text = text.replace(f"{CLOUD_BOX}rn-cloud.sh", command)
    uses = list(re.finditer(r"rn-cloud\.sh", text))
    if not uses or any(not text.endswith(f"RN_BOT={name} {CLOUD_BOX}", 0, m.start()) for m in uses):
        raise ValueError(f"cloud-uninstall ({language}) for Bot {name}: an rn-cloud.sh command without RN_BOT={name}")
    return text


def render_prompt(template, language="en", *, team_name=None, mac_user=None, mac_home=None, repo_url=None,
                  relay_name=None, mac_machine=None, level=None, mode=None, challenge=None, release_tag=None,
                  release_commit=None, cloud_checksums=None, bot_selector=None, team_hash=None):
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language: {language}; choose en or zh-CN")
    if template not in TEMPLATES:
        raise ValueError(f"unknown prompt: {template}")
    if relay_name is not None and relay_name_problem(relay_name):
        raise ValueError(relay_name_problem(relay_name))
    if bot_selector is not None and relay_name_problem(bot_selector):
        raise ValueError(relay_name_problem(bot_selector))
    if template == "cloud-uninstall":
        if team_hash is not None:
            raise ValueError("a team hash applies only to an added Bot's routine prompt")
        block = prompt_block(template, language)
        if bot_selector is not None:
            block = selector_uninstall(block, language, bot_selector)
        script, digest = cloud_uninstall_script(team_name, mac_machine, relay_name, level, mode, challenge)
        values = uninstall_values(team_name, mac_machine, relay_name, level, mode, challenge)
        values.update({"<script-sha256>": digest, "<cloud-script>": script.rstrip("\n")})
        return substitute(block, values)
    if bot_selector is not None:
        if template != "routine":
            raise ValueError("a Bot selector applies only to the routine and cloud-uninstall prompts")
        if relay_name is not None and relay_name != bot_selector:
            raise ValueError(f"the Bot selector {bot_selector} differs from the Bot address name {relay_name}")
        if team_name is None or team_hash is None:
            raise ValueError(f"the routine prompt for Bot {bot_selector} needs the team name and the team hash")
        relay_name = bot_selector
    elif team_hash is not None and template != "cloud-add-bot":
        raise ValueError("a team hash applies only to an added Bot's routine prompt")
    block = prompt_block(template, language)
    if template == "cloud-add-bot":
        return substitute(block, add_bot_values(relay_name, team_name, team_hash))
    # mac_user is retained for offline previews made with the earlier CLI.
    if mac_home is None and mac_user is not None:
        mac_home = f"/Users/{mac_user}"
    values = {"<team-name>": team_name, "<mac-user>": mac_user, "<mac-home>": mac_home,
              "<repo-url>": shlex.quote(repo_url) if repo_url is not None else None, "<relay-name>": relay_name}
    values.update(release_values(release_tag, release_commit, cloud_checksums))
    # One pass: replacement values remain literal, including &, backslashes, and placeholder-like text.
    text = substitute(block, values)
    if bot_selector is not None:
        return selector_routine(text, language, bot_selector, team_name, team_hash)
    return text


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
    parser.add_argument("--level", choices=("bot", "identity", "all"), help="cloud-uninstall: uninstall level")
    parser.add_argument("--mode", choices=("normal", "residual"), help="cloud-uninstall: normal or residual")
    parser.add_argument("--challenge", help="cloud-uninstall: 16 hex digits, new for each uninstall run")
    parser.add_argument("--release-tag", help="cloud-setup: the release tag the cloud checks out")
    parser.add_argument("--release-commit", help="cloud-setup: the commit of that tag")
    parser.add_argument("--cloud-checksums", help="cloud-setup: one line of '<sha256>  box/<file>' per cloud file")
    parser.add_argument("--bot-selector", help="routine: the added Bot's address name (needs --team-name, --team-hash); "
                        "cloud-uninstall: the added Bot whose rn-cloud.sh commands the prompt uses")
    parser.add_argument("--team-hash", help="routine with --bot-selector, cloud-add-bot: 16 hex digits")
    args = parser.parse_args()
    try:
        print(render_prompt(args.template, args.lang, team_name=args.team_name,
                            mac_user=args.mac_user, mac_home=args.mac_home, repo_url=args.repo_url,
                            relay_name=args.relay_name, mac_machine=args.mac_machine, level=args.level,
                            mode=args.mode, challenge=args.challenge, release_tag=args.release_tag,
                            release_commit=args.release_commit, cloud_checksums=args.cloud_checksums,
                            bot_selector=args.bot_selector, team_hash=args.team_hash), end="")
    except (OSError, ValueError) as error:
        parser.exit(1, f"prompt: {error}\n")


if __name__ == "__main__":
    main()
