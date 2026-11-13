#!/usr/bin/env python3
"""Offline checks for bilingual documentation in a standalone public checkout."""
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from render_prompt import render_prompt

ROOT = Path(__file__).resolve().parents[1]
FENCE = re.compile(r"^```[^\n]*\n.*?^```[ \t]*$", re.M | re.S)
LINK = re.compile(r"\[[^\]\n]+\]\(([^)\s]+)\)")


def anchors(text):
    result, counts = set(), {}
    for heading in re.findall(r"^#{1,6} (.+)$", FENCE.sub("", text), re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")
        number = counts.get(slug, 0)
        counts[slug] = number + 1
        result.add(f"{slug}-{number}" if number else slug)
    return result


MESSAGES = ROOT / "scripts/messages"
LANGUAGES = ("en", "zh-CN")
KEY = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+")
SHELL_USE = re.compile(r"(?<![\w-])(?:t|tq|die|ask|usage_error)[ \t]+([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)\b")
PY_USE = re.compile(r"(?<![\w.])t\(\s*[\"']([a-z][a-z0-9_]*(?:\.[a-z0-9_]+)+)[\"']")
DYNAMIC = (re.compile(r"(?<![\w.])t\(\s*f?[\"']([a-z][a-z0-9_.]*)(?:[\"']\s*\+|\{)"),
           re.compile(r"\b([a-z][a-z0-9_]*\.[a-z0-9_]*)\$"))


def code_files():
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and MESSAGES not in path.parents and not path.name.startswith("test_"):
            if path.suffix in (".sh", ".py") or path.name in ("install.sh", "update.sh", "check.sh", "uninstall.sh"):
                yield path


def check_messages():
    errors, tables = [], {}
    for lang in LANGUAGES:
        table = {}
        for path in sorted((MESSAGES / lang).glob("*.tsv")):
            where = path.relative_to(ROOT)
            for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
                if not line:
                    continue
                key, sep, text = line.partition("\t")
                if not sep or not KEY.fullmatch(key):
                    errors.append(f"{where}:{number}: expected key<TAB>text with a dotted lowercase key")
                    continue
                if key in table:
                    errors.append(f"{where}:{number}: duplicate key {key}")
                if re.search(r"%(?!s|%)", text.replace("%%", "")):
                    errors.append(f"{where}:{number}: {key}: only %s and %% are allowed")
                if re.search(r"\\(?![nt\\])", text.replace("\\\\", "")):
                    errors.append(f"{where}:{number}: {key}: only \\n, \\t and \\\\ are allowed")
                table[key] = text
        tables[lang] = table
    en, zh = tables["en"], tables["zh-CN"]
    for key in sorted(set(en) ^ set(zh)):
        errors.append(f"messages: {key} is in only one language")
    for key in sorted(set(en) & set(zh)):
        if en[key].replace("%%", "").count("%s") != zh[key].replace("%%", "").count("%s"):
            errors.append(f"messages: {key} has a different number of %s in en and zh-CN")
    code = {path: path.read_text(encoding="utf-8", errors="replace") for path in code_files()}
    prefixes = {prefix for text in code.values() for pattern in DYNAMIC for prefix in pattern.findall(text)}
    used = set()
    for path, text in code.items():
        for key in (SHELL_USE if path.suffix != ".py" else PY_USE).findall(text):
            used.add(key)
            if key not in en and not key.startswith(tuple(prefixes)):
                errors.append(f"{path.relative_to(ROOT)}: unknown message key {key}")
    joined = "\n".join(code.values())
    for key in sorted(set(en) - used):
        if key not in joined and not key.startswith(tuple(prefixes)):
            errors.append(f"messages: {key} is not used")
    return errors


def check_docs():
    errors = check_messages()
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        errors.append("VERSION: expected major.minor.patch")
    for name in ("README.md", "README.zh-CN.md"):
        if f"**v{version}**" not in (ROOT / name).read_text(encoding="utf-8"):
            errors.append(f"{name}: project version differs from VERSION")
    for name in ("CHANGELOG.md", "CHANGELOG.zh-CN.md"):
        if not re.search(rf"^## v{re.escape(version)} — \d{{4}}-\d{{2}}-\d{{2}}$",
                         (ROOT / name).read_text(encoding="utf-8"), re.M):
            errors.append(f"{name}: missing release entry for VERSION")
    documents = sorted(ROOT.rglob("*.md")) + [ROOT / "llms.txt"]
    for path in documents:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".md":
            other = (path.with_name(path.name.replace(".zh-CN.md", ".md"))
                     if path.name.endswith(".zh-CN.md") else path.with_name(path.stem + ".zh-CN.md"))
            if not other.is_file():
                errors.append(f"{path.relative_to(ROOT)}: missing translation {other.name}")
            if f"]({other.name})" not in "\n".join(text.splitlines()[:6]):
                errors.append(f"{path.relative_to(ROOT)}: missing top language switch")
        for target in LINK.findall(FENCE.sub("", text)):
            url = urlsplit(target)
            if url.scheme or url.netloc:
                continue
            resolved = (path.parent / unquote(url.path)).resolve() if url.path else path
            if ROOT not in resolved.parents and resolved != ROOT:
                errors.append(f"{path.relative_to(ROOT)}: link leaves distribution: {target}")
            elif not resolved.exists():
                errors.append(f"{path.relative_to(ROOT)}: broken link: {target}")
            elif url.fragment and resolved.suffix == ".md":
                if unquote(url.fragment) not in anchors(resolved.read_text(encoding="utf-8")):
                    errors.append(f"{path.relative_to(ROOT)}: missing anchor: {target}")
    for template in ("routine", "cloud-setup"):
        try:
            en = render_prompt(template, "en")
            zh = render_prompt(template, "zh-CN")
            placeholders = lambda text: set(re.findall(r"<(?:repo-url|team-name|mac-home|invite-link|relay-name|"
                                                       r"release-tag|release-commit|cloud-checksums)>", text))
            if placeholders(en) != placeholders(zh):
                errors.append(f"{template}: translated placeholders differ")
            if template == "cloud-setup":
                if placeholders(en) != {"<repo-url>", "<team-name>", "<mac-home>", "<invite-link>", "<relay-name>",
                                        "<release-tag>", "<release-commit>", "<cloud-checksums>"}:
                    errors.append("cloud-setup: expected placeholders are missing")
                block = lambda text: re.findall(r"^bash -s <<'AGENT_MESH_SETUP'\n.*?^AGENT_MESH_SETUP$", text,
                                                re.M | re.S)
                if len(block(en)) != 1 or block(en) != block(zh):
                    errors.append("cloud-setup: the AGENT_MESH_SETUP block must appear once and match in both languages")
            if template == "routine":
                for lang, text in (("en", en), ("zh-CN", zh)):
                    if placeholders(text) != {"<team-name>", "<relay-name>"}:
                        errors.append(f"routine ({lang}): expected placeholders <team-name> and <relay-name>")
                    for fixed in ("/bot", "bot-cloud", "bot-inbox", "含 bot"):
                        if fixed in text:
                            errors.append(f"routine ({lang}): fixed Bot address {fixed!r}; use <relay-name>")
        except (OSError, ValueError) as error:
            errors.append(str(error))
    try:
        found = lambda text: set(re.findall(r"<(?:team-name|mac-machine|relay-name|level|mode|challenge|"
                                            r"script-sha256|cloud-script)>", text))
        expected = {"<team-name>", "<mac-machine>", "<relay-name>", "<level>", "<mode>", "<challenge>"}
        for lang in ("en", "zh-CN"):
            text = render_prompt("cloud-uninstall", lang)
            if found(text) != expected:
                errors.append(f"cloud-uninstall ({lang}): expected placeholders {sorted(expected)}")
            if text.count("AGENT_MESH_UNINSTALL_EOF") != 2 or "```" in text:
                errors.append(f"cloud-uninstall ({lang}): the cloud script is not embedded once as a heredoc")
    except (OSError, ValueError) as error:
        errors.append(str(error))
    for lang, name in (("en", "AGENTS.reason-network.md"), ("zh-CN", "AGENTS.reason-network.zh-CN.md")):
        lines = (ROOT / "templates" / name).read_text(encoding="utf-8").splitlines()
        starts = [i for i, line in enumerate(lines) if line.startswith("<!-- agent-mesh:bot-address")]
        ends = [i for i, line in enumerate(lines) if line == "<!-- /agent-mesh:bot-address -->"]
        if (len(starts) != 1 or len(ends) != 1 or ends[0] != starts[0] + 2
                or lines[starts[0]] != f"<!-- agent-mesh:bot-address lang={lang} -->"
                or "`<team-name>/<relay-name>`" not in lines[starts[0] + 1]):
            errors.append(f"templates/{name}: expected one generated Bot address block (lang={lang}) with one "
                          "line holding `<team-name>/<relay-name>`")
    en = (ROOT / "PROTOCOL.md").read_text(encoding="utf-8")
    zh = (ROOT / "PROTOCOL.zh-CN.md").read_text(encoding="utf-8")
    vectors = lambda text: set(re.findall(r"\b[0-9a-f]{64}\b", text))
    if len(vectors(en)) != 5 or vectors(en) != vectors(zh):
        errors.append("PROTOCOL: protocol test-vector signatures differ or are missing")
    return errors


if __name__ == "__main__":
    errors = check_docs()
    for error in errors:
        print(f"FAIL {error}")
    if not errors:
        print("PASS release version, bilingual pairs, local links and anchors, prompt placeholders, template blocks, "
              "protocol vectors, message catalogs")
    raise SystemExit(bool(errors))
