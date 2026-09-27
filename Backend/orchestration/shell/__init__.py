"""Shell policy for the v0.6 governed shell.

``classifier.py`` holds the UX/tripwire tier decision. The command executor
lives in ``orchestration.shell_exec`` (the sidecar). The sandbox — not this
package — is the security boundary. See ``docs/contracts/credential-scoping.md``.
"""
