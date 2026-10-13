"""The planner, its confirmation matcher and the manager verifier are gone.

Nothing under Backend/ may import them, and workflow_planner must not define
the two names that used to live there.
"""
from django.test import SimpleTestCase


class LegacyPlannerRemovalTests(SimpleTestCase):

    FORBIDDEN_MODULES = {
        "orchestration.manager_verifier",
        "orchestration.intent_parser",
    }
    FORBIDDEN_NAMES = {
        "plan_user_request",
        "looks_like_confirmation",
    }

    def test_no_module_imports_the_removed_entry_points(self):
        import ast
        from pathlib import Path

        backend_root = Path(__file__).resolve().parent.parent
        offenders = []
        for path in backend_root.rglob("*.py"):
            if "migrations" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    if node.module in self.FORBIDDEN_MODULES:
                        offenders.append(f"{path.name} imports {node.module}")
                    for alias in node.names:
                        if alias.name in self.FORBIDDEN_NAMES:
                            offenders.append(f"{path.name} imports {alias.name}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in self.FORBIDDEN_MODULES:
                            offenders.append(f"{path.name} imports {alias.name}")
        self.assertEqual(offenders, [], offenders)

    def test_the_planner_module_no_longer_defines_them(self):
        import ast
        from pathlib import Path

        from orchestration import workflow_planner

        tree = ast.parse(Path(workflow_planner.__file__).read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertNotIn("plan_user_request", defined)
        self.assertNotIn("looks_like_confirmation", defined)
