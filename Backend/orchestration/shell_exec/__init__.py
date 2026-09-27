"""Shell execution (v0.6 governed shell).

The sidecar in this package is deliberately dumb: it runs one command and
returns stdout/stderr/exit code. It holds no policy and no Kazi credentials —
all policy lives in Kazi's orchestration layer. See
``docs/contracts/credential-scoping.md``.
"""
