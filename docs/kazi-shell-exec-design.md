# Kazi: Elevated Shell Execution & Agent Sandbox — Design Doc

Status: draft for implementation
Owner: Bedan
Scope: adds a new connector class + isolated execution host to Kazi Core. Does not change the Agent Loop, Tool Executor, or Connector Registry contracts — it plugs into them.

---

## 1. Goal

Today, the Agent Loop can only act through registered connectors with typed schemas (`orchestration/action_catalog.py`). That's correct for known capabilities (email, payments, travel) but breaks for open-ended requests with no connector — "ping my ISP", "check why this container keeps restarting", "see what's using port 8080." The user shouldn't get a dead end; the agent should be able to open a real shell on a machine built for this, explore, and report back — the same way Claude Code or OpenHands drive a shell toward an outcome instead of only calling fixed tools.

Non-goal for this phase: full autonomous skill synthesis (writing new permanent connectors from a shell session). That's a follow-on (Section 8) — get exploration + safety right first.

---

## 2. Architecture Overview

```
Client (WebSocket / HTTP)
  |
  v
ChatConsumer -> OrchestrationCoordinator -> ContextManager
  |
  v
Agent Loop (think -> act -> observe)
  |
  v
Tool Executor  --------------------->  Security Policy
  |                                     - prompt injection check
  |  action = "run_shell"               - command risk classification (NEW)
  |                                     - autopilot policy gate (NEW)
  v
Connector Registry
  |
  v
ShellConnector (new, orchestration/connectors/shell_connector.py)
  |
  | HTTPS/SSH, mutual-TLS
  v
Shell Exec Daemon  <-- runs on the isolated agent host, NOT the Kazi backend host
  |
  v
Persistent shell session (bash/pwsh) on the agent's own machine
```

Key decision: **isolation is at the machine/network layer, not per-task containers.** A dedicated mini PC (clean Linux or Windows install) is the agent's own machine — nothing else lives on it. State persists across tasks on purpose: installed tools, scripts, and shell history accumulate, which is what makes open-ended exploration ("ping my ISP", "diagnose this") get better over time instead of restarting from zero every call. This trades Kazi's normal per-parameter sanitization (which only works on typed connector args) for isolation at the network boundary and coarse command classification — see Section 5.

---

## 3. New Components

### 3.1 `ShellConnector`
- Lives in `orchestration/connectors/`, discovered by the existing Connector Registry auto-discovery (built-in path — no entry-point packaging needed for a first-party connector).
- Registers one action in the Action Catalog: `run_shell`, params `{ command: str, cwd: str (optional), timeout_s: int (optional, capped) }`.
- Risk level: **high** by default in the Action Catalog — this routes it through the existing confirmation/approval gate machinery (durable agent-loop approvals / `WorkflowApprovalRecord(kind=agent_loop)`) rather than requiring new infrastructure for approvals. Autopilot (Section 6) is what lets specific commands skip that pause.
- Talks to the Shell Exec Daemon over the network — does not shell out locally on the Kazi backend host under any circumstance.

### 3.2 Shell Exec Daemon (new service, runs on the mini PC)
- A small always-on service (FastAPI/Flask over HTTPS, or an SSH-based executor if you'd rather reuse `paramiko`/OpenSSH instead of writing a custom protocol).
- Auth: mutual TLS or an SSH key scoped only to this daemon's service account — never the Kazi backend's own credentials, never a key that also has access to prod systems.
- Executes the command in the agent's persistent shell session, captures stdout/stderr/exit code, streams back as the tool's "observe" payload — this is a drop-in fit for the existing ReAct loop, no change needed there.
- Enforces its own hard limits independent of what Kazi sends: max execution time, max output size, no `sudo` without a separate explicit elevation flag (see 3.4).

### 3.3 Command Risk Classifier (extends `orchestration/security_policy.py`)
Kazi's existing security policy sanitizes typed params and detects prompt injection — neither applies to a raw shell string. Add a classification step specific to `run_shell`:

| Tier | Examples | Behavior |
|---|---|---|
| Safe / read-only | `ping`, `dig`, `traceroute`, `cat`, `ls`, `ps`, `curl` (GET, allowlisted hosts) | Runs immediately, no approval needed |
| Mutating / scoped | `mkdir`, `git clone`, `pip install`, `npm install`, writes inside agent's own workdir | Runs under autopilot policy (Section 6); logged, not blocked |
| Destructive / high-risk | `rm -rf`, `dd`, disk/partition tools, firmware writes, anything touching `/`, network scanning tools (`nmap` against non-owned ranges), privilege escalation (`sudo`, `su`) | Always pauses for human approval via existing durable approval flow — autopilot cannot override this tier |
| Denied | outbound to credential stores, prod DB hosts, anything outside the agent host's firewall allowlist | Blocked at the daemon's network layer before it reaches the classifier — defense in depth |

Classification is pattern/allowlist based (command name + argument shape), not semantic understanding of intent — keep it conservative and easy to audit; false positives (an extra approval prompt) are cheap, false negatives are not.

**Implementation note:** build this as a generic pre-tool-call/post-tool-call hook registered against the Tool Executor, not code hardcoded inside `security_policy.py` specifically for `run_shell`. Any future high-risk connector should be able to register its own classifier against the same hook point without touching this one — matches the existing "connectors register additional actions" extensibility pattern already used by the Connector Registry.

### 3.4 Autopilot Policy Engine
- Config object (per room or per user) with fields: `enabled: bool`, `max_tier_auto: "safe" | "mutating"`, `network_allowlist: [hosts]`, `require_approval_above: tier`.
- Sits between the Command Risk Classifier and the existing approval-checkpoint system: if the classified tier is at or below what autopilot allows, execution proceeds and is logged; above that, it creates a `WorkflowApprovalRecord` exactly like other high-risk actions today, so the operator UI you already have for approve/reject/rerun works unmodified.
- Destructive tier is a hard floor — never auto-approved regardless of config, matching the "explicit deny rules always win" pattern used by comparable agent sandboxes (Claude Code's sandboxing keeps `rm`/`rmdir` against root or home paths gated even in its most permissive auto-allow mode; the same floor applies here).

### 3.5 Audit Logger
- Every `run_shell` call and its full output goes into an append-only log tied to the room/session, separate from the general chat transcript — this is your primary audit trail in place of structured tool-call records, which is what you give up by moving to raw shell.
- Snapshot the mini PC's disk/VM state on a schedule (or before any mutating-tier batch of commands) so a bad session is a rollback, not an incident.

---

## 4. Data Flow Example

User sends: "Can you ping my ISP?"

```
1. ChatConsumer receives message
2. Agent Loop: no connector matches "ping" as a typed action
3. LLM decides: tool_use(run_shell, {"command": "ping -c 4 <isp_gateway>"})
4. Tool Executor:
   - Security Policy: prompt-injection check passes
   - Command Risk Classifier: tier = safe (read-only network probe)
   - Autopilot: safe tier within configured max_tier_auto -> auto-approved
5. ShellConnector -> Shell Exec Daemon on mini PC
6. Daemon runs the command in the persistent shell, returns stdout + exit code
7. LLM sees result, replies in natural language
8. Agent Loop yields "done"
```

Compare: "Can you clear old logs, I think `rm -rf /var/log/*` is fine?"

```
3. tool_use(run_shell, {"command": "rm -rf /var/log/*"})
4. Command Risk Classifier: tier = destructive (rm -rf pattern)
   -> autopilot cannot override; WorkflowApprovalRecord created
5. Agent Loop pauses; operator (Bedan) approves or rejects via existing workflow API
6. On approval, ShellConnector executes; on reject, LLM is told and proposes an alternative
```

---

## 5. Isolation Model

- **Network**: mini PC on its own VLAN/subnet. Outbound firewall allowlist by default (DNS, the specific hosts the user's tasks need); no route to Kazi's Postgres, Redis, payment provider, or any credential store.
- **Physical/account**: dedicated OS user account for the agent; no shared credentials with Bedan's own login. No cloud provider keys or `.aws`/`.ssh` configs for other systems present on the box.
- **What you're explicitly not getting** (documented tradeoff, not an oversight): per-command sandboxing like a fresh container gives you. If the agent's persistent shell gets compromised or corrupted, the blast radius is bounded by the network/account isolation above, not by a clean-slate teardown. Snapshots (Section 3.5) are the recovery mechanism.

---

## 6. Config Surface (for the coding agent implementing this)

```yaml
shell_exec:
  daemon_host: "10.x.x.x"
  auth: "mtls"  # or "ssh_key"
  timeout_s_default: 120
  timeout_s_max: 900
  output_bytes_max: 200000

autopilot:
  enabled: false          # off by default; opt-in per room
  max_tier_auto: "safe"   # "safe" | "mutating" — destructive is never auto
  network_allowlist: []
  require_approval_above: "safe"
```

---

## 7. Implementation Phases

1. **Daemon + connector, no autopilot.** Every `run_shell` call pauses for approval regardless of tier. Prove the plumbing (Tool Executor -> Connector -> Daemon -> observe) works end to end.
2. **Risk classifier + audit log.** Add tiering and the append-only log; still gate everything behind approval, but now the approval prompt shows the classified tier.
3. **Autopilot for `safe` tier only.** Turn on auto-approval for read-only commands; everything else still pauses.
4. **Autopilot for `mutating` tier**, with network allowlist enforced at the daemon, snapshot-before-batch enabled.
5. **(Future, separate design doc) Procedural memory + skill promotion.** Kazi's memory is currently 3-tier (hot context, entity tracking, persistent summaries) — all episodic. Add a 4th tier, procedural memory: when autopilot notices a shell sequence succeeding repeatedly, it drafts it into procedural memory as a reviewable "recipe" (command sequence + what it accomplished), not straight into the Connector Registry. A human promotes a recipe to a full registered connector only from that staging area — this is the validation-gate step from the earlier discussion, now with a concrete home instead of being an ungrounded "review it somehow" step. Deliberately deferred: get exploration and containment right before adding "the agent decides what becomes permanent."

---

## 8. Open Questions / Explicitly Deferred

- Windows vs Linux mini PC: daemon design above assumes POSIX shell; a PowerShell variant needs its own command-tier table (different destructive patterns — e.g. `Remove-Item -Recurse -Force`, registry edits).
- Multi-room isolation if more than one Kazi room gets shell access to the same box — likely needs per-room subdirectories/users on the daemon side, not shared state.
- What "destructive tier" means for Windows firmware/BitLocker/disk tools specifically — needs its own allowlist pass, don't reuse the Linux one blindly.

---

## 9. Reference Points

- OpenHands' Docker-runtime pattern (client/server split — backend ships an action over an API to an executor that has its own bash shell) is the closest published architecture for the connector <-> daemon split above, adapted here from per-task containers to one persistent host.
- Claude Code's sandboxing model is the reference for the risk-tiering approach: explicit deny rules and destructive-path floors hold even in the most permissive auto-allow mode, and a separate classifier (not the base LLM) is what reviews actions in unattended/auto mode rather than a human every time.
