---
name: night-sweep
description: The Night Hawk evening sweep - host check, scratch cleanup, and internal system sweeps.
tools: run_command
stage: active
---

# Night Sweep

## When to use it
Between 23:00 and 06:00, or whenever the owner asks Night Hawk for the nightly sweep.

## Required inputs and access
- Governed shell access through the sandbox sidecar.
- Read access to Kazi run logs and the scratch directory.

## The sequence of work
1. Confirm the host is alive and Kazi's processes are up.
2. Clean stale scratch logs and temp files (night-cleanup).
3. Run the internal sweeps: workflow health, expired grants, stuck approvals, skill curation.
4. Record results and receipts in the run history.

## How to validate the result
Every step leaves output in the execution result; failures surface in the ops inbox with summaries.

## What to return
A compact summary: host status, files cleaned, sweeps run, anything needing attention.

## What requires approval
Anything beyond the scoped cleanup: sends, purchases, deletes outside Kazi scratch, publishing, and production changes.
