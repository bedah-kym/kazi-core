"""Shell command classification (v0.6 governed shell).

This is UX + a tripwire, **never** the security boundary. The sandbox
(non-root, read-only rootfs, ``--cap-drop=ALL``, network off by default) is
what actually bounds a command, so a false negative here costs nothing. See
``docs/v0.6-roadmap.md`` §4.3 and ``docs/contracts/credential-scoping.md``.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional

TIER_SAFE = "safe"
TIER_BOUNDED = "bounded"
TIER_DESTRUCTIVE = "destructive"
TIER_DENIED = "denied"

PROFILES = ("open", "standard", "locked")

_SEGMENT_SPLIT = re.compile(r"\|\||&&|[;|&\n]")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_WRAPPERS = {"command", "env", "time", "nice", "nohup", "stdbuf", "exec"}

_ROOT_BINARIES = {"sudo", "su", "doas", "pkexec"}
_ROOT_PATTERNS = (
    re.compile(r"\bchmod\s+[ug]\+s\b"),
    re.compile(r"\bchown\s+root\b"),
)

# Short, honest blocklist. A miss is intentional: the sandbox still bounds it.
_DESTRUCTIVE_PATTERNS = (
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f\b"),
    re.compile(r"\brm\s+-[a-z]*f[a-z]*r\b"),
    re.compile(r"\bdd\b.*\bof="),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bmkswap\b"),
    re.compile(r"\bwipefs\b"),
    re.compile(r"\bshred\b"),
    re.compile(r"\bfdisk\b"),
    re.compile(r"\bparted\b"),
    re.compile(r"\bblkdiscard\b"),
    re.compile(r":\s*\(\s*\)\s*\{"),
    re.compile(r">\s*/dev/(sd|nvme|hd)"),
    re.compile(r"\bchmod\s+-R\s+777\s+/"),
    re.compile(r"\bmv\s+/\*"),
)

_NETWORK_BINARIES = {
    "ping", "ping6", "dig", "host", "nslookup", "curl", "wget", "ssh", "scp",
    "sftp", "ftp", "nc", "ncat", "netcat", "telnet", "traceroute", "tracepath",
    "whois", "rsync", "openssl", "apt", "apt-get", "apk", "yum", "dnf",
    "pip", "pip3", "npm", "yarn", "pnpm", "cargo", "go", "docker", "podman",
    "git",
}
_NETWORK_SUBCOMMANDS = {
    "git": {"clone", "fetch", "pull", "push", "ls-remote", "submodule"},
    "pip": {"install", "download"},
    "pip3": {"install", "download"},
    "npm": {"install", "i", "ci", "publish", "view", "ping"},
    "yarn": {"add", "install"},
    "cargo": {"install", "add", "publish"},
    "go": {"get", "install"},
    "docker": {"pull", "push", "run", "build", "login", "search"},
}


def _basename(word: str) -> str:
    return word.rsplit("/", 1)[-1].strip("\"'")


def _words(segment: str) -> List[str]:
    tokens = [raw for raw in segment.strip().split() if not _ASSIGNMENT.match(raw)]
    while tokens and _basename(tokens[0]) in _WRAPPERS:
        tokens.pop(0)
    return tokens


def _segments(command: str) -> Iterable[str]:
    return _SEGMENT_SPLIT.split(command or "")


def needs_root(command: str) -> bool:
    for segment in _segments(command):
        words = _words(segment)
        if words and _basename(words[0]) in _ROOT_BINARIES:
            return True
    return any(pattern.search(command or "") for pattern in _ROOT_PATTERNS)


def needs_network(command: str) -> bool:
    for segment in _segments(command):
        words = _words(segment)
        if not words:
            continue
        binary = _basename(words[0])
        if binary not in _NETWORK_BINARIES:
            continue
        subcommands = _NETWORK_SUBCOMMANDS.get(binary)
        if subcommands is None:
            return True
        if len(words) > 1 and words[1].strip("\"'") in subcommands:
            return True
    return False


def is_destructive(command: str) -> bool:
    return any(pattern.search(command or "") for pattern in _DESTRUCTIVE_PATTERNS)


def classify_command(
    command: str,
    profile: str = "standard",
    allowlist: Optional[Iterable[str]] = None,
) -> Dict[str, object]:
    """Return one tier for ``(command, profile)``.

    Tiers: ``safe`` (auto), ``bounded`` (needs network/root escalation),
    ``destructive`` (matches the tripwire), ``denied`` (out of envelope).
    """
    profile = str(profile or "standard")
    if profile not in PROFILES:
        profile = "standard"

    root = needs_root(command)
    network = needs_network(command)
    destructive = is_destructive(command)

    if destructive:
        tier, reason = TIER_DESTRUCTIVE, "matches the destructive tripwire"
    elif root:
        if profile == "open":
            tier, reason = TIER_BOUNDED, "needs root (inline confirmation on open)"
        else:
            tier, reason = TIER_DENIED, f"needs root; not allowed on the {profile} profile"
    elif network:
        if profile == "locked":
            tier, reason = TIER_DENIED, "needs network; the locked profile has none"
        elif profile == "open":
            tier, reason = TIER_SAFE, "needs network; the open profile allows it"
        else:
            tier, reason = TIER_BOUNDED, "needs network (allowlist / inline confirmation)"
    else:
        tier, reason = TIER_SAFE, "read-only or within the sandbox envelope"

    return {
        "tier": tier,
        "reason": reason,
        "needs_root": root,
        "needs_network": network,
        "destructive": destructive,
        "profile": profile,
        "allowlisted": bool(allowlist),
    }
