"""Rule 8: no secrets reach the repository. Cheap pre-commit substitute that runs in CI.

Scope is deliberately the files git would carry, not the working tree: a real `.env` holding a
real API key is correct and expected on the core host, and must never make this suite red.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "data",
    "logs",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "build",
    "dist",
}
SKIP_SUFFIXES = {".db", ".parquet", ".png", ".jpg", ".ico", ".html"}
# Never scanned: this file (it holds the patterns) and any real env file (gitignored by design).
SKIP_NAMES = {"test_no_secrets.py", ".env.example"}

PATTERNS = {
    "deepseek/openai style key": re.compile(r"\bsk-[A-Za-z0-9]{24,}\b"),
    "telegram bot token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private key block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "mt5 password assignment": re.compile(r"^\s*MT5_PASSWORD\s*=\s*\S+", re.M),
}


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _candidate_files() -> list[Path]:
    """Files git would carry: tracked plus untracked-but-not-ignored. Falls back to a filtered walk."""
    listing = _git("ls-files", "--cached", "--others", "--exclude-standard")
    if listing is not None:
        paths = [ROOT / line for line in listing.splitlines() if line.strip()]
    else:
        paths = [
            p
            for p in ROOT.rglob("*")
            if not any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts)
            # no git here: exclude env files by name, since we cannot ask what is ignored
            and not p.name.startswith(".env")
        ]
    return [p for p in paths if p.is_file() and p.suffix not in SKIP_SUFFIXES and p.name not in SKIP_NAMES]


def test_no_secret_patterns_in_committable_files():
    hits = []
    for p in _candidate_files():
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, rx in PATTERNS.items():
            if rx.search(text):
                hits.append(f"{p.relative_to(ROOT)}: {name}")
    assert not hits, "possible secrets in files git would carry:\n" + "\n".join(hits)


def test_env_file_is_ignored_and_untracked():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in [line.strip() for line in gitignore]
    tracked = _git("ls-files", ".env", ".env.local")
    if tracked is None:
        return  # no git here; the .gitignore assertion above still holds
    assert tracked.strip() == "", ".env must never be tracked"


def test_env_example_exists_and_has_no_values_for_secrets():
    example = ROOT / ".env.example"
    assert example.exists(), ".env.example is the template committed to the repo; do not rename it to .env"
    text = example.read_text(encoding="utf-8")
    for key in ("MT5_PASSWORD", "DEEPSEEK_API_KEY", "TELEGRAM_BOT_TOKEN"):
        m = re.search(rf"^{key}=(.*)$", text, re.M)
        assert m is not None and m.group(1).strip() == "", f"{key} must be empty in .env.example"
    assert re.search(r"^ALLOW_LIVE=false$", text, re.M), "ALLOW_LIVE must default to false"
