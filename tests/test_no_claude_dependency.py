"""Decision D5: Claude is build-time only. Nothing under src/ may import Anthropic SDKs or shell out to the claude CLI."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

IMPORT_RE = re.compile(r"^\s*(from|import)\s+(anthropic|claude_agent_sdk|claude_code)\b", re.M)
SHELL_RE = re.compile(r"(subprocess|os\.system|os\.popen|shlex)[^\n]*\bclaude\b", re.I)
IMPORT_STRING_RE = re.compile(r"import_module\(\s*['\"](anthropic|claude)", re.I)


def test_src_has_no_claude_imports_or_shellouts():
    offenders = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        for rx in (IMPORT_RE, SHELL_RE, IMPORT_STRING_RE):
            m = rx.search(text)
            if m:
                offenders.append(f"{py.relative_to(ROOT)}: {m.group(0).strip()}")
    assert not offenders, "runtime must not depend on Claude:\n" + "\n".join(offenders)


def test_pyproject_has_no_anthropic_dependency():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = list(data["project"].get("dependencies", []))
    for group in data["project"].get("optional-dependencies", {}).values():
        deps.extend(group)
    bad = [d for d in deps if re.match(r"\s*(anthropic|claude)", d, re.I)]
    assert not bad, bad
