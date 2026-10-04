"""Which network a shell command gets, and which hosts it may reach.

One place decides this so the risk gate and the connector can never disagree:
if the gate says a command runs without a prompt because the proxy bounds it,
the connector sends it through the proxy. See
``docs/plans/2026-10-shell-egress-proxy.md``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

NETWORK_NONE = "none"
NETWORK_BRIDGE = "bridge"
NETWORK_PROXY = "proxy"
MAX_HOSTS = 200

# Package registries and the CDNs they download from. Approved by the
# maintainer on 2026-10-04.
#
# An approved host is trusted for upload as well as download. These defaults
# accept writes with a token the caller supplies, so a script in the workspace
# could send data to them: github.com (git push), registry.npmjs.org and
# registry.yarnpkg.com (publish), rubygems.org (push), hackage.haskell.org
# (upload). They are here because install/clone does not work without them.
#
# Deliberately left out:
#   - relays that fetch any origin on request (proxy.golang.org, sum.golang.org,
#     goproxy.io) — add them yourself if you build Go;
#   - open object storage (S3, GCS, Azure Blob) and paste/webhook/tunnel hosts;
#   - write-only API hosts (upload.pypi.org, crates.io, www.nuget.org,
#     api.github.com, gist.github.com, uploads.github.com);
#   - browser CDNs (cdn.jsdelivr.net, unpkg.com, cdnjs.cloudflare.com): no
#     package manager needs them and anyone can host content there.
DEFAULT_HOSTS = (
    # Python
    "pypi.org", "files.pythonhosted.org", "bootstrap.pypa.io",
    # Node
    "registry.npmjs.org", "registry.yarnpkg.com", "repo.yarnpkg.com", "nodejs.org",
    # Rust (the sparse index and the download CDN; not the crates.io API)
    "index.crates.io", "static.crates.io", "static.rust-lang.org", "sh.rustup.rs",
    # Go toolchain downloads only (module proxy left out on purpose)
    "go.dev", "dl.google.com",
    # Ruby
    "rubygems.org", "index.rubygems.org",
    # Java / Kotlin
    "repo.maven.apache.org", "repo1.maven.org", "plugins.gradle.org",
    "plugins-artifacts.gradle.org", "services.gradle.org", "downloads.gradle.org",
    # PHP
    "repo.packagist.org", "packagist.org", "getcomposer.org",
    # .NET
    "api.nuget.org",
    # Elixir, Perl, Haskell
    "repo.hex.pm", "builds.hex.pm", "cpan.metacpan.org", "www.cpan.org",
    "hackage.haskell.org", "downloads.haskell.org",
    # Conda
    "conda.anaconda.org", "repo.anaconda.com",
    # OS packages (https mirrors)
    "dl-cdn.alpinelinux.org", "deb.debian.org", "security.debian.org",
    "archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com",
    # GitHub: clone over https and release downloads
    "github.com", "codeload.github.com", "raw.githubusercontent.com",
    "objects.githubusercontent.com", "release-assets.githubusercontent.com",
)


def _setting(name: str, default: Any) -> Any:
    try:
        from django.conf import settings

        return getattr(settings, name, default)
    except Exception:
        return default


def proxy_enabled() -> bool:
    return bool(_setting("SHELL_EGRESS_PROXY", False))


def network_mode(classification: Dict[str, Any], profile_name: str) -> str:
    """``none`` | ``proxy`` | ``bridge`` for an already-classified command.

    The proxy only exists for the ``standard`` profile, and only carries
    HTTPS; a raw-socket command still needs the open bridge (and a prompt).
    """
    if not classification.get("needs_network"):
        return NETWORK_NONE
    if (
        str(profile_name) == "standard"
        and proxy_enabled()
        and not classification.get("raw_network")
    ):
        return NETWORK_PROXY
    return NETWORK_BRIDGE


def default_hosts() -> List[str]:
    """Operator override (``SHELL_EGRESS_DEFAULT_HOSTS``, may be empty) or the built-in list."""
    configured = _setting("SHELL_EGRESS_DEFAULT_HOSTS", None)
    return list(DEFAULT_HOSTS if configured is None else configured)


def approved_hosts(room_id: Any) -> List[str]:
    """This room's grants + the operator allowlist + default registries.

    Only well-formed public host names survive (an IP in the operator
    allowlist is a ping target, not something the proxy can pin). A grant
    lookup failure drops the grants, never the command's bounds. Explicit
    approvals come first so the size cap can only ever drop defaults.
    """
    from orchestration.shell_exec.squid import clean_hosts

    hosts: List[Any] = []
    try:
        from orchestration.shell.host_grants import active_hosts

        hosts.extend(active_hosts(room_id))
    except Exception as exc:
        logger.warning("Host grant lookup failed for room %s: %s", room_id, exc)
    hosts.extend(_setting("SHELL_EXEC_NETWORK_ALLOWLIST", []) or [])
    hosts.extend(default_hosts())
    cleaned = clean_hosts(hosts)
    if len(cleaned) > MAX_HOSTS:
        logger.warning("Room %s has %d approved hosts; only the first %d are used.", room_id, len(cleaned), MAX_HOSTS)
    return cleaned[:MAX_HOSTS]
