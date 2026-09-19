@AGENTS.md

## Claude Code specifics
- Use plan mode for protected paths, migrations, payments, approvals and contracts.
- Delegate exploration to a subagent. Keep the main context for the change.
- Before a PR, run the `reviewer` subagent on the diff. Address each finding.
- `.claude/hooks/protect_paths.py` blocks edits to protected paths. A block means: write a plan and ask. Do not route around it.
- Keep this file short. If you already do something correctly without an instruction, delete the instruction.
