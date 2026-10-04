# Plan: enforced shell egress + per-room host grants (auto mode Phase 2)

Status: approved by Bedan (2026-10-03 start; 2026-10-04 stock proxy, default
host list, approver switch). Built behind `SHELL_EGRESS_PROXY` (default
**off**). Verified against a real Docker daemon (Engine 29.8, Squid 5.7) on
2026-10-04 with `scripts/verify_shell_egress.py`: 24 of 24 checks pass.
Reviewed twice before the PR; the second review's findings are folded in.

Follows `2026-10-shell-auto-mode.md` (Phase 1) and
`2026-10-taint-across-turns.md`. Closes open question 1 of `v0.7-brief.md` §9.

## Standing guidance this plan follows

From the amendment posted on #134 (`docs/proposals/2026-09-hardening-issues.md`):

- "Do not hand-write an egress proxy. Use a stock, widely used one."
- "An allowed domain is still an exfiltration path."
- "Acceptance: test that a command cannot reach a non-allowlisted host,
  including by DNS."

A first build of this phase ignored the first two and was thrown away after
review (a hand-written proxy that could be fronted over plain HTTP, and a rule
that let tainted runs use approved hosts unprompted). This is the second design.

## Goal

On the `standard` profile, a network command can only reach hosts a human
approved, whether or not it was prompted for. On an untainted run,
HTTPS-capable commands (`pip`, `npm`, `git` over https, `curl`) then need no
prompt at all.

## What it does not do

A tainted run still asks for every network command. A host allowlist cannot
make that safe: registries accept uploads with a token the injected text can
supply, and a room stays tainted for 15 minutes after any shell or web output.
Removing that prompt needs read-only enforcement (GET/HEAD only), which means
TLS interception. That is a separate, later piece of work.

## The rule (`standard`, proxy on)

| Command | Runs on | Untainted | Tainted |
|---|---|---|---|
| local (no network) | `none` | auto | auto |
| HTTPS-capable (`curl`, `pip`, `npm`, `git` https, …) | proxy | **auto** | ask, then proxy |
| script that asks for `network: "bridge"` | proxy | auto | ask, then proxy |
| publish / push / upload (`npm publish`, `git push`, `curl -d`, …) | proxy | ask | ask |
| raw network (`ping`, `dig`, `ssh`, `nc`, git over ssh) | open bridge | ask | ask |
| destructive | — | ask | ask |

Proxy off = exactly Phase 1. `locked` and `open` are unchanged.

## Design

```
exec container ──(its own internal, host-isolated network)──> its own Squid ──> approved hosts, HTTPS only
```

1. **One stock Squid per command.** For a proxied command the sidecar creates a
   network and a Squid container for that command alone, and removes both when
   it ends. The Squid's allowlist is exactly that room's approved hosts, so no
   authentication is needed and rooms never share a network segment. Kazi
   ships no proxy code: `shell_exec/squid.py` only renders `squid.conf` and a
   host list and reads the access log. Image: Debian's `squid-openssl`, built
   from `shell_exec/egress/Dockerfile` on first use.
2. **Network.** `docker network create --internal -o
   com.docker.network.bridge.gateway_mode_ipv4=isolated`. Internal: no route
   out and no external DNS. Isolated gateway: the bridge has no host address,
   so the sandbox cannot reach services the host binds on `0.0.0.0` (Kazi's own
   Redis/Postgres). Docker silently accepts network options it does not know
   (observed), so an old engine would create a plain internal network. Two
   guards: the engine must be 28 or newer, and after creating the network the
   sidecar checks it really is internal with no host gateway. Either failing
   fails the command; there is no fallback to the open bridge.
3. **Squid policy.** CONNECT only, port 443 only, IP-literal targets refused,
   destination must be on the allowlist, destination may not resolve to a
   private/loopback/link-local/metadata address. Then Squid peeks at the TLS
   ClientHello: if it asks for a name (SNI) that is not approved, the tunnel is
   cut, so an approved name cannot front for another site on the same CDN. A
   hello with no name at all is tunnelled (observed), but only to the approved
   host's own address. Nothing is decrypted.
4. **Blocked hosts come back to the human.** Squid's access log gives the
   refused names; the connector tells the model "blocked: `x.com` — the user
   can approve it by replying exactly `allow host x.com`". No new pause type
   in the agent loop.
5. **Host grants.** `ShellHostGrant(room, host, created_by, expires_at,
   revoked_at)`: room-scoped, expiring (`SHELL_HOST_GRANT_DAYS`, 30), receipted
   in the same transaction. Created only by the exact chat reply or the
   management command; never by model or tool output.
   `SHELL_HOST_GRANT_APPROVERS` = `members` (default) or `staff`. Any member
   may revoke.
6. **Approved set for a command** = `SHELL_EXEC_NETWORK_ALLOWLIST` +
   default hosts + the room's grants. Only plain public names survive.
7. **Start-up, not request time.** With the flag on, `run_shell_exec` checks
   the engine, builds the proxy image if missing, and sweeps networks and
   containers a crashed sidecar left behind (`label=kazi.egress=1`), or
   refuses to start. A request never waits on an image build. Teardown runs
   even when a command times out, fails to start, or the request is cancelled.
8. **One decision function.** `shell/egress.network_mode()` is called by both
   the risk gate and the connector, so what the gate approves as "proxied" is
   what the sidecar is asked to run (pinned by a test).

## Default hosts

Approved by Bedan on 2026-10-04 ("as many defaults as possible"); the list is
`shell/egress.DEFAULT_HOSTS` (44 package registries and the CDNs they
download from). Five of them accept writes with a token the caller supplies
and are included because install/clone does not work without them:
`github.com`, `registry.npmjs.org`, `registry.yarnpkg.com`, `rubygems.org`,
`hackage.haskell.org`. Left out on purpose, because an approved host is trusted
for upload as well as download:

- relays that fetch any origin on request: `proxy.golang.org`,
  `sum.golang.org`, `goproxy.io` (add them yourself to build Go);
- open object storage (S3, GCS, Azure Blob) and paste/webhook/tunnel hosts;
- write-only API hosts: `upload.pypi.org`, `crates.io`, `www.nuget.org`,
  `api.github.com`, `gist.github.com`, `uploads.github.com`;
- browser CDNs (`cdn.jsdelivr.net`, `unpkg.com`, `cdnjs.cloudflare.com`): no
  package manager needs them and anyone can host content there.

`SHELL_EGRESS_DEFAULT_HOSTS` replaces the list (empty value = no defaults).

## Touches

- New: `shell_exec/squid.py`, `shell_exec/egress/Dockerfile`, `shell/egress.py`,
  `shell/host_grants.py`, `scripts/verify_shell_egress.py`, tests.
- Changed: `shell_exec/backends.py`, `shell_exec/daemon.py`,
  `connectors/shell_connector.py`, `shell/classifier.py`, `shell/chat_intents.py`,
  `coordinator.py`, `orchestration/models.py`, `workflows/ui_*`, settings, docs.
- Protected: `tool_executor.py` (`_run_command_risk_info`), one migration
  (`orchestration/0003_shellhostgrant`), `docs/contracts/credential-scoping.md`
  (Rule 2, version 1.1). No compose change.
- New dependency: none in Python. One image built on the sidecar host.

## Risk class of any new action

outside-world, bounded to human-approved hosts over HTTPS. Removes the prompt
for untainted HTTPS-capable commands on `standard`; the boundary that replaces
it is the per-command network plus Squid.

## Verification (executable)

- `python scripts/verify_shell_egress.py` on the sidecar host. It drives the
  real `DockerBackend`. Recorded 2026-10-04 (Docker 29.8, Squid 5.7): approved
  host reachable; unapproved host refused and reported; direct connection,
  port 80, plain HTTP and external DNS all refused; raw addresses refused in
  dotted, IPv6, decimal and hex forms; SNI for another site cut and reported;
  approved names pointing at 10.x / 127.x / 172.17.0.1 / 169.254.169.254
  refused by policy (not merely unreachable); host services unreachable (with
  a control showing they *are* reachable on a plain `--internal` network);
  `pip install` works with the registry hosts and fails naming the missing
  host without them; two concurrent commands each get only their own
  allowlist; a timed-out command leaves nothing behind.
  Overhead: about 0.9 s per proxied command.
- Unit (Docker mocked): config text, host validation, denied-log parsing,
  backend lifecycle and fail-closed paths, gate rule with the flag on and off,
  gate/connector agreement, grant store and approver switch, chat reply
  handled by the coordinator without the model, ops panel.
- Old attacks still blocked: flag off = Phase 1; tainted network asks;
  publish/upload asks; raw network asks; destructive asks.
- Injection corpus: not extended (it scores message-level detection only).

## Rollback

Set `SHELL_EGRESS_PROXY=false`. Network commands return to per-command prompts
on the open bridge. The grant table can stay; nothing reads it with the flag off.

## Known limits (stated)

- **An approved host is a place data can be sent.** On an untainted run a
  script already in the workspace (for example one written by an earlier,
  tainted run) can upload to any approved host that accepts writes, using a
  token it carries. The publish/upload check is a tripwire for plain commands,
  not a boundary. Keep the allowlist to hosts you would trust with the
  workspace's contents.
- **Inside the TLS tunnel Squid sees nothing.** A CDN that routes on the inner
  `Host:` header could still be fronted, and so could one that honours an
  Encrypted Client Hello whose outer name is an approved host (not tested).
  Major CDNs reject a Host that does not match the SNI.
- **Sub-agents** follow the same gate: an untainted sub-agent can now run a
  proxied network command without a prompt, where before anything that would
  pause was blocked.
- **The 15-minute taint window** is what stands between old untrusted text in
  the chat history and an unprompted proxied command.
- **Squid 5.7** is the Debian bookworm package (security-patched by Debian,
  end-of-life upstream). The image is built once on the sidecar host; rebuild
  it to pick up fixes.
- **HTTPS only.** Plain-HTTP package mirrors (default `apt`) do not work
  through the proxy.
- **The default exec image** (`alpine`) has no `pip`, `npm`, `git` or `curl`,
  and tools get no writable `HOME` or `/tmp`. Real build tasks need a richer
  `SHELL_EXEC_IMAGE`; changing sandbox defaults is out of scope.
- **Docker Engine 28+** is required and checked; older engines are refused. Up to roughly 30 proxied commands can run at once (one Docker
  network each, from the default address pools).
- **A crashed sidecar** can leave a labelled network/container behind
  (`docker ps -a --filter label=kazi.egress=1`).
- One Codespace-specific note: the verification host had a stale
  `iptables-legacy` FORWARD policy that blocked *all* custom-bridge traffic and
  had to be opened first. Run the script on the real host; do not rely on this
  record.

## Open questions for the human

None blocking. Later: read-only enforcement for tainted runs (TLS
interception), and a richer default exec image.
