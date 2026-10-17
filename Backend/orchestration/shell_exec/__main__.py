"""``python -m orchestration.shell_exec``: the no-Django sidecar entry point."""
from orchestration.shell_exec.server import main

if __name__ == "__main__":
    raise SystemExit(main())
