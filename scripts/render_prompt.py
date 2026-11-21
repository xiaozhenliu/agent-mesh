#!/usr/bin/env python3
"""Render installer prompts from the user-facing Markdown; no network or config reads."""
import argparse
import hashlib
import importlib.util
import re
import shlex
from pathlib import Path

from relay_names import problem as relay_name_problem

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ("en", "zh-CN")
TEMPLATES = ("routine", "cloud-setup", "cloud-uninstall", "cloud-add-bot")
CLOUD_SCRIPT = ROOT / "box" / "rn-cloud-uninstall.sh"
CLOUD_FILES = ("box/rn-cloud.sh", "box/rn-fetch.py", "box/rn-cloud-setup.sh", "box/rn-engine.py",
               "box/rn-cloud-add-bot.sh", "box/rn-routine.py", "box/rn-names.py", "prompts/routine-body.md",
               "prompts/routine-body.zh-CN.md")
HEREDOC_END = "AGENT_MESH_UNINSTALL_EOF"
TEAM = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
UNINSTALL_KEYS = ("<team-name>", "<mac-machine>", "<relay-name>", "<level>", "<mode>", "<challenge>")
CLOUD_BOX = "/workspace/agent-mesh/box/"
THREADS = "/workspace/agent-mesh/threads/"
RENDER_KEYS = re.compile(r"<(?:team-name|relay-name|team-hash|mac-user|mac-home|repo-url|release-tag|release-commit|"
                         r"cloud-checksums)>")
STUB_VERSION = 1
STEP1 = f"   bash {CLOUD_BOX}rn-cloud.sh instructions {STUB_VERSION}"
STEP3 = "   bash -c 'cd /workspace/agent-mesh || exit 1; "
FULL_COMMAND = "   bash -c 'cd /workspace/agent-mesh || exit 1; i=\"$1\"; "
_ROUTINE = []


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
        if (not all(re.fullmatch(r"[0-9a-f]{64}  (?:box|prompts)/[A-Za-z0-9._-]+", line) for line in lines)
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


def routine_module():
    if not _ROUTINE:
        spec = importlib.util.spec_from_file_location("rn_routine_render", ROOT / "box" / "rn-routine.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _ROUTINE.append(module)
    return _ROUTINE[0]


def only_line(lines, start, label):
    found = [i for i, line in enumerate(lines) if line.startswith(start)]
    if len(found) != 1:
        raise ValueError(f"{label}: expected exactly one line starting with {start.strip()!r}")
    return found[0]


def stub_selector(text, name, team_name, team_hash, reclaim=False):
    check_team("team name", team_name)
    check_team_hash(team_hash)
    lines = text.split("\n")
    one = only_line(lines, STEP1, "routine stub")
    if lines[one] != STEP1:
        raise ValueError("routine stub: step 1 is not the plain instructions command")
    lines[one] = f"   RN_BOT={name} {STEP1.strip()} '{team_name}' '{team_hash}'" + (" reclaim" if reclaim else "")
    three = only_line(lines, STEP3, "routine stub")
    command = lines[three].strip()
    if command.count("inbox/") != 3:
        raise ValueError("routine stub: the step 3 command must name inbox/ three times")
    lines[three] = f"   RN_BOT={name} " + command.replace("inbox/", f"bots/{name}/inbox/")
    return "\n".join(lines)


def full_routine(language, team_name, relay_name, bot_selector, team_hash, reclaim=False):
    if team_name is None:
        raise ValueError("the complete routine prompt needs the team name")
    check_team("team name", team_name)
    name = relay_name or bot_selector or "bot"
    slot = bot_selector or "-"
    routine = routine_module()
    values = routine.body_values(team_name, name, slot)
    body = routine.render_body(ROOT, language, team_name, name, slot)
    lines = routine.fill(prompt_block("routine-full", language), values).split("\n")
    at = only_line(lines, FULL_COMMAND, f"routine-full ({language})")
    command = lines[at].strip()
    if bot_selector is not None:
        if command.count("inbox/") != 2:
            raise ValueError(f"routine-full ({language}): the fixed command must name inbox/ twice")
        command = f"RN_BOT={bot_selector} " + command.replace("inbox/", f"bots/{bot_selector}/inbox/")
    lines[at] = "   " + command
    text = body + "\n".join(lines)
    if bot_selector is not None:
        lines = text.split("\n")
        first = only_line(lines, "R1. ", f"routine ({language})")
        step0 = substitute(prompt_block("cloud-add-bot", language), add_bot_values(slot, team_name, team_hash))
        if reclaim:
            step0 = step0.replace(f"rn-cloud-add-bot.sh '{slot}' '{team_name}' '{team_hash}'",
                                  f"rn-cloud-add-bot.sh '{slot}' '{team_name}' '{team_hash}' reclaim")
        lines[first:first] = step0.rstrip("\n").split("\n")
        text = "\n".join(lines)
    problems = []
    if bot_selector is not None:
        if any(not text.endswith(f"RN_BOT={slot} {CLOUD_BOX}", 0, m.start()) for m in re.finditer(r"rn-cloud\.sh", text)):
            problems.append(f"an rn-cloud.sh command without RN_BOT={slot}")
        if THREADS in text:
            problems.append("the first Bot's threads path")
        step0 = f"rn-cloud-add-bot.sh '{slot}' '{team_name}' '{team_hash}'" + (" reclaim" if reclaim else "")
        if sum(line.startswith("0. ") for line in text.split("\n")) != 1 or text.count(step0) != 1:
            problems.append("step 0 not exactly once")
    found = RENDER_KEYS.search(text) or re.search(r"<(?:rn-cloud|threads-dir)>", text)
    if found:
        problems.append(f"an unfilled placeholder {found[0]}")
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
                  release_commit=None, cloud_checksums=None, bot_selector=None, team_hash=None, full=False,
                  reclaim=False):
    if language not in LANGUAGES:
        raise ValueError(f"unsupported language: {language}; choose en or zh-CN")
    if template not in TEMPLATES:
        raise ValueError(f"unknown prompt: {template}")
    if relay_name is not None and relay_name_problem(relay_name):
        raise ValueError(relay_name_problem(relay_name))
    if bot_selector is not None and relay_name_problem(bot_selector):
        raise ValueError(relay_name_problem(bot_selector))
    if full and template != "routine":
        raise ValueError("--full applies only to the routine prompt")
    if reclaim and (template != "routine" or bot_selector is None):
        raise ValueError("--reclaim applies only to an added Bot's routine prompt")
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
        if team_name is None or team_hash is None:
            raise ValueError(f"the routine prompt for Bot {bot_selector} needs the team name and the team hash")
    elif team_hash is not None and template != "cloud-add-bot":
        raise ValueError("a team hash applies only to an added Bot's routine prompt")
    if template == "routine":
        if full:
            return full_routine(language, team_name, relay_name, bot_selector, team_hash, reclaim)
        block = prompt_block(template, language)
        return stub_selector(block, bot_selector, team_name, team_hash, reclaim) if bot_selector is not None else block
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
    return substitute(block, values)


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
    parser.add_argument("--cloud-checksums", help="cloud-setup: one line of '<sha256>  <file>' per cloud file")
    parser.add_argument("--bot-selector", help="routine: the added Bot's slot, the name it was added with (needs "
                        "--team-name, --team-hash; --relay-name then gives its current address name); "
                        "cloud-uninstall: the added Bot whose rn-cloud.sh commands the prompt uses")
    parser.add_argument("--team-hash", help="routine with --bot-selector, cloud-add-bot: 16 hex digits")
    parser.add_argument("--full", action="store_true",
                        help="routine: the complete prompt (rules rendered here) instead of the short stub")
    parser.add_argument("--reclaim", action="store_true",
                        help="routine with --bot-selector: the Bot was added with a name it takes back")
    args = parser.parse_args()
    try:
        print(render_prompt(args.template, args.lang, team_name=args.team_name,
                            mac_user=args.mac_user, mac_home=args.mac_home, repo_url=args.repo_url,
                            relay_name=args.relay_name, mac_machine=args.mac_machine, level=args.level,
                            mode=args.mode, challenge=args.challenge, release_tag=args.release_tag,
                            release_commit=args.release_commit, cloud_checksums=args.cloud_checksums,
                            bot_selector=args.bot_selector, team_hash=args.team_hash, full=args.full,
                            reclaim=args.reclaim), end="")
    except (OSError, ValueError) as error:
        parser.exit(1, f"prompt: {error}\n")


if __name__ == "__main__":
    main()
