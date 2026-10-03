---
name: night-cleanup
description: Safe cleanup of stale Kazi scratch files - old logs, repro scripts, and temp archives.
tools: run_command
stage: active
---

# Night Cleanup

## When to use it
During the nightly sweep, or when the owner asks Night Hawk to tidy the scratch directory.

## Required inputs and access
- The scratch directory (%TEMP%\opencode).
- Shell access through the sandbox sidecar.

## The sequence of work
1. List scratch files older than one day.
2. Delete only .log, .py, .json, and .zip files past the cutoff.
3. Never touch control scripts (*.cmd, *.ps1), .env files, or the running logs.
4. Report the number of files removed.

## How to validate the result
The command echoes "night-cleanup removed N stale scratch files"; N must match the listed set.

## What to return
One summary line with the removed count.

## What requires approval
Deleting anything outside the scratch directory, or any file newer than the cutoff.
