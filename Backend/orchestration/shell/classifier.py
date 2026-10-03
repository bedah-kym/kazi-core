"""Shell command classification (v0.6 governed shell).

This is UX + a tripwire, **never** the security boundary. The sandbox
(non-root, read-only rootfs, ``--cap-drop=ALL``, network off by default) is
what actually bounds a command, so a false negative here costs nothing. See
``docs/v0.6-roadmap.md`` §4.3 and ``docs/contracts/credential-scoping.md``.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlparse

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
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f\b", re.IGNORECASE),
    re.compile(r"\brm\s+-[a-z]*f[a-z]*r\b", re.IGNORECASE),
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
    # PowerShell cmdlets with unambiguous names, wherever they appear.
    re.compile(r"\bremove-item\b[^|;&\n]*\s-(?:r|fo)\w*", re.IGNORECASE),
    re.compile(r"\b(?:clear-disk|format-volume|remove-partition)\b", re.IGNORECASE),
)

# Windows hosts (the `open` profile runs cmd/PowerShell with no sandbox). These
# verbs are short words, so they only count as the command of a segment —
# never inside a flag or path (`docker run --rm`, `git rm -r`, `grep rm -r`).
_WINDOWS_SEGMENT_PATTERNS = (
    re.compile(r"^(?:rmdir|rd)\b.*?/s\b", re.IGNORECASE),
    re.compile(r"^(?:del|erase)\b.*?/s\b", re.IGNORECASE),
    re.compile(r"^(?:del|erase)\b(?=.*/q\b).*[*?]", re.IGNORECASE),
    re.compile(r"^format(?:\.com)?\s+[a-z]:", re.IGNORECASE),
    re.compile(r"^(?:ri|rm|del|erase|rd|rmdir)\b.*\s-rec\w*", re.IGNORECASE),
    re.compile(r"^diskpart\b", re.IGNORECASE),
    re.compile(r"^cipher\s+/w", re.IGNORECASE),
)
_POWERSHELL_PREFIX = re.compile(
    r"^(?:powershell|pwsh)(?:\.exe)?\s+(?:-\w+\s+)*?-(?:command|c)\s+[\"']?", re.IGNORECASE,
)

_NETWORK_BINARIES = {
    "ping", "ping6", "dig", "host", "nslookup", "curl", "wget", "ssh", "scp",
    "sftp", "ftp", "nc", "ncat", "netcat", "telnet", "traceroute", "tracepath",
    "whois", "rsync", "openssl", "apt", "apt-get", "apk", "yum", "dnf",
    "pip", "pip3", "npm", "yarn", "pnpm", "cargo", "go", "docker", "podman",
    "git", "ip", "ifconfig", "route", "ss", "netstat", "arp",
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

_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_HOST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}$")
_IP_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
_HOST_COMMANDS = {
    "ping", "ping6", "dig", "host", "nslookup", "ssh", "scp", "telnet",
    "nc", "ncat", "netcat", "whois", "traceroute", "tracepath",
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
    if any(pattern.search(command or "") for pattern in _DESTRUCTIVE_PATTERNS):
        return True
    for segment in _segments(command):
        text = _POWERSHELL_PREFIX.sub("", " ".join(_words(segment)))
        if any(pattern.search(text) for pattern in _WINDOWS_SEGMENT_PATTERNS):
            return True
    return False


def _clean_token(token: str) -> str:
    token = token.strip("\"'")
    if "@" in token:
        token = token.rsplit("@", 1)[-1]
    return token


def extract_hosts(command: str) -> List[str]:
    """Best-effort list of hosts a command will reach.

    Heuristic on purpose: the host allowlist is a UX shortlist, not a firewall.
    """
    hosts: List[str] = []
    for segment in _segments(command):
        words = _words(segment)
        if not words:
            continue
        binary = _basename(words[0])
        candidates: List[str] = []
        fallback = ""
        for token in words[1:]:
            clean = _clean_token(token)
            if not clean or clean[0] in "-+":
                continue
            if _URL_RE.match(clean):
                candidate = urlparse(clean).hostname or ""
            elif _IP_RE.match(clean) or _HOST_RE.match(clean):
                candidate = clean
            else:
                candidate = ""
            if candidate:
                candidates.append(candidate)
            elif binary in _HOST_COMMANDS and not clean.isdigit():
                fallback = clean
        for candidate in candidates:
            if candidate not in hosts:
                hosts.append(candidate)
        if not candidates and fallback and fallback not in hosts:
            hosts.append(fallback)
    return hosts


def classify_command(
    command: str,
    profile: str = "standard",
    allowlist: Optional[Iterable[str]] = None,
    requested_network: Optional[str] = None,
) -> Dict[str, object]:
    """Return one tier for ``(command, profile)``.

    Tiers: ``safe`` (auto), ``bounded`` (needs network/root escalation),
    ``destructive`` (matches the tripwire), ``denied`` (out of envelope).
    ``requested_network="bridge"`` lets the caller ask for network for a command
    the classifier can't infer (e.g. a script that pings internally).
    """
    profile = str(profile or "standard")
    if profile not in PROFILES:
        profile = "standard"

    root = needs_root(command)
    network = needs_network(command) or str(requested_network or "").strip().lower() == "bridge"
    destructive = is_destructive(command)
    hosts = extract_hosts(command)
    allow = {str(host).strip().lower() for host in (allowlist or []) if str(host).strip()}
    allowlisted = bool(hosts) and all(host.lower() in allow for host in hosts)

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
        "hosts": hosts,
        "allowlisted": allowlisted,
    }
