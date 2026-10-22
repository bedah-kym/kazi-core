# Plan: T-F6 per-message work
Status: approved by Bedan on 2026-10-22 (touches the protected `agent_loop.py`, within the Limits below). Coder: DeepSeek. Reviewed and live-tested by the lead.
Style: deliverables and acceptance checks; the coder chooses the approach and explains departures in the PR.

## Goal
One chat turn reads each thing it needs once. The travel-era entity tracker no longer runs on every tool call, and the harness no longer writes a "Completed: â€¦" line the next turn treats as fact.

## Evidence
Research `plans/2026-10-compute-memory-research.md` Q6, read on `main` at `181f1f7`. Line numbers are from that commit; the roadmap's `agent_loop.py:1622-1628` is stale.
- **Before the loop starts, about 15â€“25 database and cache round trips repeat:**
  - the user is read twice (`consumers.py:780`; the user is already in `scope`);
  - the chatroom 3â€“4 times (`consumers.py:1544`, `:642`, `:1626`; `context_manager.py:176`);
  - participants twice (`consumers.py:1549`; `notifications/services.py:163`);
  - user plus profile twice (`user_preferences.py:311`; `profile_store.py:17`);
  - the loop state twice, a full JSON parse each time (`coordinator.py:367-374`; `agent_loop.py:2757-2760`);
  - the shell profile twice (`coordinator.py:331-344`);
  - `current_chat.save()` does a full-row update after an M2M add (`consumers.py:906-907`).
- **History is an N+1:** two queries per message for `member.User` (`consumers.py:1602, 1631-1633`), up to 16 for eight messages.
- **The entity tracker runs on every tool call.** `update_memory_state` costs a cache read, two cache writes, a telemetry write and a `RoomContext` read and maybe write (`memory_state.py:177-260`). It is called at `agent_loop.py:1528, 2019, 2477`.
- **The "Completed" line claims success regardless of outcome.** `agent_loop.py:2617-2624` writes "Completed: <tool names> (<seconds>s)" into the memory summary that the next turn's prompt reads (`coordinator.py:260`), whatever the tools' results were. T-D3 already replays the last turn's real calls and results.
- **A dead telemetry branch remains.** No telemetry event carries `params` or `input` any more (found in Phase 1 QA, PR #256), so the named-entity branch of `telemetry_rollups.extract_entity_facts` (`:206`, used at `:653`) has no producer. Its tests feed it a shape nothing emits.

## Deliverables
1. **One read per thing per turn.** Every database and cache read between "message received" and "loop start" (consumer and coordinator) is either removed or justified in a table in the PR: what, why, and its count before and after. Room, member, participants, user profile, loop state and shell profile are each read once per turn and passed along.
2. **No N+1 in history.** Loading a room's recent history costs a fixed number of queries, whatever the message count.
3. **Entity tracker off the hot path.** No per-tool-call entity-tracker work in the loop. If something the model sees depends on it, say what and keep that part, with its cost in the PR.
4. **No harness success claim in memory.** The loop no longer writes "Completed: â€¦" into the summary the next turn reads. The record of what ran is T-D3's, which carries the results.
5. **Dead telemetry branch removed**, with its tests, or a source named that does not write values to a plain-text file. Choose, and say which.
6. **No behaviour change for the user.** Same replies, approvals, pending-confirmation and budget/quota resumes, presence and notifications.

## Acceptance
- **Measurement, in the PR:** the query and cache counts for one ordinary turn on main and on the branch, taken with `connection.execute_wrapper()` or an equivalent counter. Target: no repeated read of the same row in one turn, and history at a fixed query count.
- **Tests that fail on main:**
  - a turn's query count stays at or under the measured new number (`assertNumQueries` or a counter);
  - history query count does not grow with message count;
  - no `update_memory_state` call from the loop per tool call;
  - no "Completed:" summary written.
- **Existing coverage stays green:** confirmation, budget and quota resume, autopilot, and T-D3 replay.
- **Suites,** run serially: orchestration, chatbot, workflows, users, Api. Plus flake8, bandit and `check_boundaries.py`.
- **Lead, live (room 2 and a non-staff room):**
  - five ordinary turns;
  - a confirmation turn;
  - the eight-command task;
  - a turn after a failed tool, so the next turn must not believe the failed call completed;
  - time from send to first token, before and after.

## Limits
- **Protected file allowed:** `agent_loop.py`, only to remove the entity-tracker calls and the "Completed" write, and to read the loop state once if the coordinator cannot do it alone. Any other protected file: stop and say so.
- No migration, no new dependency, no prompt-wording change beyond removing the "Completed" line.

## Risk class
Read-only refactor of reads plus removal of two writes. The risk is behavioural (a stale object passed along instead of re-read), which is why every removed read is listed and every resume path stays covered.

## Rollback
Revert the PR. No data shape changes.
