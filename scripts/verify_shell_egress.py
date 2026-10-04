#!/usr/bin/env python3
"""Verify the shell egress proxy against a REAL Docker daemon.

The unit tests mock Docker, so they cannot prove the sandbox network is
actually closed. Run this on the host that will run the shell sidecar BEFORE
setting SHELL_EGRESS_PROXY=true:

    python scripts/verify_shell_egress.py

It drives the same ``DockerBackend`` code the sidecar uses (no re-implemented
docker flags), one check per sandboxed command. Every "refused" check looks
for a specific refusal by the proxy, so a missing tool or a broken container
cannot pass as "refused". Needs: Docker Engine 28+, outbound HTTPS, and DNS
for pypi.org and nip.io. Exit code 0 = every check passed.
"""
from __future__ import annotations

import asyncio
import os
import shlex
import shutil
import socket
import subprocess  # nosec B404 - fixed argv, verification helper
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "Backend"))

from orchestration.shell_exec.backends import (  # noqa: E402
    DockerBackend,
    ShellExecConfig,
    _docker,
    prepare_egress,
)

EXEC_IMAGE = os.environ.get("KAZI_VERIFY_EXEC_IMAGE", "python:3.12-alpine")
LISTEN_PORT = 18765
PRIVATE_NAMES = ["10.0.0.1.nip.io", "169.254.169.254.nip.io", "127.0.0.1.nip.io", "172.17.0.1.nip.io"]
RAW_TARGETS = ["1.1.1.1:443", "[::1]:443", "2130706433:443", "0x7f.1:443"]

FETCH = """
import urllib.request
try:
    r = urllib.request.urlopen({url!r}, timeout=25)
    print("RESULT ok", r.status)
except Exception as e:
    print("RESULT fail", type(e).__name__, str(e)[:160])
"""

DIRECT = """
import socket
try:
    socket.create_connection(("1.1.1.1", 443), timeout=6).close()
    print("RESULT ok")
except Exception as e:
    print("RESULT fail", type(e).__name__)
"""

DNS = """
import socket
try:
    print("RESULT ok", socket.getaddrinfo("example.com", 443)[0][4][0])
except Exception as e:
    print("RESULT fail", type(e).__name__)
"""

GATEWAY = """
import socket
ip = socket.gethostbyname(socket.gethostname())
gw = ip.rsplit(".", 1)[0] + ".1"
try:
    socket.create_connection((gw, {port}), timeout=5).close()
    print("RESULT reachable", gw)
except Exception as e:
    print("RESULT unreachable", gw, type(e).__name__)
"""

# Each probe ends in exactly one word:
#   tunnelled      - a TLS session with the real destination was established
#   refused        - the proxy reset the connection, answered non-200, or answered
#                    "200" and then served ITS OWN 403 under its unused certificate
#   dropped        - the proxy opened the tunnel but cut it during the TLS hello
#   proxy-status:N - the proxy served its own non-403 error (it tried to connect)
#   error:...      - something else
# The last two fail the check they appear in.
PROBES = """
import socket, ssl
def tunnel(target, sni):
    try:
        s = socket.create_connection(("proxy", 3128), timeout=15)
        s.settimeout(15)
        s.sendall(("CONNECT %s HTTP/1.1\\r\\nHost: %s\\r\\n\\r\\n" % (target, target)).encode())
        head = s.recv(4096).decode("latin-1")
    except (ConnectionResetError, BrokenPipeError):
        return "refused"
    except Exception as e:
        return "error:" + type(e).__name__
    if not head or head.split(" ")[1:2] != ["200"]:
        return "refused"
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        t = ctx.wrap_socket(s, server_hostname=sni or None)
    except Exception:
        return "dropped"
    if b"kazi-egress-unused" not in t.getpeercert(binary_form=True):
        return "tunnelled"
    try:
        t.sendall(b"GET / HTTP/1.1\\r\\nHost: x\\r\\nConnection: close\\r\\n\\r\\n")
        status = t.recv(200).decode("latin-1").split(" ")[1:2]
    except Exception:
        status = ["none"]
    return "refused" if status == ["403"] else "proxy-status:" + status[0]
def plain(url):
    try:
        s = socket.create_connection(("proxy", 3128), timeout=15)
        s.settimeout(15)
        s.sendall(("GET %s HTTP/1.1\\r\\nHost: pypi.org\\r\\n\\r\\n" % url).encode())
        head = s.recv(4096).decode("latin-1")
    except (ConnectionResetError, BrokenPipeError):
        return "refused"
    except Exception as e:
        return "error:" + type(e).__name__
    return "refused" if not head or head.split(" ")[1:2] == ["403"] else "answered:" + head[:40].replace(" ", "_")
out = [tunnel(target, sni) for target, sni in {cases!r}]
if {with_plain!r}:
    out.append(plain("http://pypi.org/simple/pip/"))
print("RESULT", " ".join(out))
"""


class Verifier:
    def __init__(self) -> None:
        self.failed = False
        self.root = Path(tempfile.mkdtemp(prefix="kazi-egress-verify-"))
        self.config = ShellExecConfig(
            root=self.root, image=EXEC_IMAGE, egress_proxy=True, timeout_default=120, timeout_max=180,
        )
        self.backend = DockerBackend(self.config)

    def report(self, ok: bool, label: str, detail: str = "") -> None:
        if not ok:
            self.failed = True
        print(("PASS  " if ok else "FAIL  ") + label + (f"   [{detail}]" if detail and not ok else ""))

    async def shell(self, command: str, hosts, **extra):
        result = await self.backend.execute(
            command, room_id="verify", network="proxy", allowed_hosts=list(hosts), **extra,
        )
        line = next((ln for ln in result.stdout.splitlines() if ln.startswith("RESULT")), "")
        detail = line or f"exit={result.exit_code} stderr={result.stderr.strip()[-200:]}"
        return line.split()[1:], result, detail

    async def run(self, code: str, hosts, **extra):
        return await self.shell("python -c " + shlex.quote(code), hosts, **extra)

    async def probe(self, cases, hosts, with_plain=False):
        return await self.run(PROBES.format(cases=cases, with_plain=with_plain), hosts)

    async def main(self) -> int:
        code, version = await _docker("version", "--format", "{{.Server.Version}}")
        print(f"Docker server: {version if code == 0 else 'NOT AVAILABLE'}")
        if code != 0:
            print("FAIL  docker daemon is not reachable")
            return 1
        error = await prepare_egress(self.config)
        self.report(error is None, "engine is new enough, proxy image is built, leftovers swept", str(error))
        if error:
            return 1
        await _docker("pull", "-q", EXEC_IMAGE, timeout=300)

        listener = subprocess.Popen(  # nosec B603 - fixed argv
            # nosec B104 - deliberately listens on every interface: the check is that the sandbox cannot reach it
            [sys.executable, "-m", "http.server", str(LISTEN_PORT), "--bind", "0.0.0.0"],  # nosec B104
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            self.listener_up = self.wait_for_listener()
            await self.checks()
        finally:
            listener.terminate()
        await self.leftovers()
        shutil.rmtree(self.root, ignore_errors=True)

        print()
        if self.failed:
            print("One or more checks FAILED. Do NOT enable SHELL_EGRESS_PROXY on this host.")
        else:
            print("All checks passed on this host: sandboxed commands can only open HTTPS tunnels to approved hosts.")
            print("This proves the network boundary, not that an approved host is a safe place to send data.")
        return 1 if self.failed else 0

    @staticmethod
    def wait_for_listener() -> bool:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", LISTEN_PORT), timeout=1).close()
                return True
            except OSError:
                time.sleep(0.1)
        return False

    async def checks(self) -> None:
        words, result, detail = await self.run(FETCH.format(url="https://pypi.org/simple/pip/"), ["pypi.org"])
        self.report(words[:2] == ["ok", "200"], "approved host is reachable through the proxy", detail)
        if words[:1] != ["ok"]:
            print("      (everything below depends on the proxy working; fix this first)")

        words, result, detail = await self.run(FETCH.format(url="https://example.com/"), ["pypi.org"])
        self.report(
            words[:1] == ["fail"] and "example.com" in result.blocked_hosts,
            "unapproved host is refused, and reported for the user to approve",
            f"{detail} blocked_hosts={result.blocked_hosts}",
        )

        words, _, detail = await self.run(DIRECT, ["pypi.org"])
        self.report(words[:1] == ["fail"], "a direct connection that skips the proxy fails", detail)

        words, _, detail = await self.run(DNS, ["pypi.org"])
        self.report(words[:1] == ["fail"], "external DNS does not resolve inside the sandbox", detail)

        # Fronting gets its own command so the report can only name THIS name.
        words, result, detail = await self.probe(
            [("pypi.org:443", "pypi.org"), ("pypi.org:443", "example.org")], ["pypi.org"],
        )
        self.report(words[:1] == ["tunnelled"], "TLS to an approved host with a matching name is tunnelled", detail)
        self.report(
            words[1:2] in (["dropped"], ["refused"]) and "example.org" in result.blocked_hosts,
            "TLS asking for another site (SNI) through an approved host is cut and reported",
            f"{detail} blocked_hosts={result.blocked_hosts}",
        )

        cases = [("example.com:443", "example.com"), ("pypi.org:80", "pypi.org")]
        cases += [(target, "one.one.one.one") for target in RAW_TARGETS]
        cases += [(name + ":443", name) for name in PRIVATE_NAMES]
        labels = ["unapproved", "port80"] + RAW_TARGETS + PRIVATE_NAMES + ["plain"]
        words, _, detail = await self.probe(cases, ["pypi.org"] + PRIVATE_NAMES, with_plain=True)
        got = dict(zip(labels, words))
        self.report(got.get("unapproved") == "refused", "a tunnel to an unapproved host is refused", detail)
        self.report(got.get("port80") == "refused", "a tunnel to a port other than 443 is refused", detail)
        for target in RAW_TARGETS:
            self.report(got.get(target) == "refused", f"a tunnel to a raw address is refused ({target})", detail)
        for name in PRIVATE_NAMES:
            self.report(got.get(name) == "refused",
                        f"an approved name that points at a private address is refused, not tried ({name})", detail)
        self.report(got.get("plain") == "refused", "plain HTTP is refused even for an approved host", detail)

        words, _, detail = await self.probe([("pypi.org:443", "")], ["pypi.org"])
        print(f"INFO  a TLS hello with no server name through an approved host is: {' '.join(words) or detail}"
              " (it can only reach that host's own address)")

        self.report(self.listener_up, "the host test listener is up (needed for the next check)")
        words, _, detail = await self.run(GATEWAY.format(port=LISTEN_PORT), ["pypi.org"])
        self.report(words[:1] == ["unreachable"], "the sandbox cannot reach services listening on the Docker host", detail)
        await self.gateway_control()

        pip = ("python -m pip install -q --no-cache-dir --disable-pip-version-check "
               "--target /workspace/verify_pkg idna && echo RESULT ok || echo RESULT fail")
        words, result, detail = await self.shell(pip, ["pypi.org", "files.pythonhosted.org"])
        self.report(words[:1] == ["ok"], "pip installs a package with only the registry hosts approved",
                    f"{detail} stderr={result.stderr.strip()[-200:]}")
        words, result, detail = await self.shell(pip.replace("verify_pkg", "verify_pkg2"), ["pypi.org"])
        self.report(
            words[:1] == ["fail"] and "files.pythonhosted.org" in result.blocked_hosts,
            "the same install fails, naming the missing host, when the download CDN is not approved",
            f"{detail} blocked_hosts={result.blocked_hosts}",
        )

        fetch = FETCH.format(url="https://pypi.org/simple/pip/")
        (allowed, _, d1), (denied, _, d2) = await asyncio.gather(
            self.run(fetch, ["pypi.org"]), self.run(fetch, ["example.com"]),
        )
        self.report(
            allowed[:1] == ["ok"] and denied[:1] == ["fail"],
            "two commands running at once each get only their own allowlist", f"{d1} / {d2}",
        )

        _, result, _ = await self.shell("sleep 30", ["pypi.org"], timeout_s=3)
        self.report(result.exit_code == 124, "a command that times out is stopped", f"exit={result.exit_code}")

    async def gateway_control(self) -> None:
        """Show the host IS reachable on a plain --internal network, so the check above means something."""
        name = "kazi-egress-verify-control"
        await _docker("network", "rm", name)
        code, _ = await _docker("network", "create", "--internal", name)
        if code != 0:
            print("INFO  control skipped: could not create a plain internal network")
            return
        try:
            _, out = await _docker(
                "run", "--rm", f"--network={name}", EXEC_IMAGE, "python", "-c", GATEWAY.format(port=LISTEN_PORT),
            )
        finally:
            await _docker("network", "rm", name)
        if "RESULT reachable" in out:
            print("INFO  control: on a plain --internal network the host listener IS reachable, so isolation matters here")
        else:
            print("INFO  control: this Docker blocks host access even on a plain --internal network")

    async def leftovers(self) -> None:
        _, containers = await _docker("ps", "-aq", "--filter", "label=kazi.egress=1")
        _, networks = await _docker("network", "ls", "-q", "--filter", "label=kazi.egress=1")
        left = sorted(p.name for p in (self.root / "egress").glob("*")) if (self.root / "egress").exists() else []
        self.report(not containers and not networks and not left,
                    "no proxy, network or policy file is left behind, including after the timeout",
                    f"containers={containers!r} networks={networks!r} files={left}")


if __name__ == "__main__":
    sys.exit(asyncio.run(Verifier().main()))
