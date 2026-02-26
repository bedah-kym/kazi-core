import json
import os
from typing import Any, Dict, List

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.management.base import BaseCommand

from orchestration.intent_parser import parse_intent
from orchestration.workflow_planner import plan_user_request


class Command(BaseCommand):
    help = "Run golden scenario evaluations for orchestration planning and intent parsing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=None,
            help="Path to golden_scenarios.json",
        )
        parser.add_argument(
            "--allow-llm",
            action="store_true",
            help="Allow LLM calls during evaluation.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Limit number of scenarios to evaluate.",
        )

    def handle(self, *args, **options):
        path = options.get("path")
        allow_llm = options.get("allow_llm")
        limit = options.get("limit")

        if not path:
            path = os.path.join(
                settings.BASE_DIR,
                "orchestration",
                "eval",
                "golden_scenarios.json",
            )

        if not os.path.exists(path):
            self.stderr.write(f"Scenario file not found: {path}")
            return

        with open(path, "r", encoding="utf-8") as handle:
            scenarios = json.load(handle)

        if limit:
            scenarios = scenarios[:limit]

        totals = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
        failures: List[str] = []

        for scenario in scenarios:
            totals["total"] += 1
            scenario_id = scenario.get("id") or f"scenario_{totals['total']}"
            requires_llm = bool(scenario.get("requires_llm"))
            if requires_llm and not allow_llm:
                totals["skipped"] += 1
                self.stdout.write(f"[SKIP] {scenario_id} (requires LLM)")
                continue

            message = scenario.get("message") or ""
            history = scenario.get("history") or ""
            preferences = scenario.get("preferences") or {}

            expected_mode = scenario.get("expected_mode")
            expected_actions = scenario.get("expected_actions") or []
            expected_intent_action = scenario.get("expected_intent_action")

            passed = True
            details: List[str] = []

            if expected_mode or expected_actions:
                plan = async_to_sync(plan_user_request)(
                    message,
                    history,
                    user_id=None,
                    preferences=preferences,
                )
                mode = plan.get("mode")
                if expected_mode and mode != expected_mode:
                    passed = False
                    details.append(f"mode {mode} != {expected_mode}")

                if expected_actions:
                    definition = plan.get("workflow_definition") or {}
                    actions = [
                        step.get("action")
                        for step in (definition.get("steps") or [])
                        if isinstance(step, dict)
                    ]
                    for action in expected_actions:
                        if action not in actions:
                            passed = False
                            details.append(f"missing action {action}")

            if expected_intent_action:
                intent = async_to_sync(parse_intent)(
                    message,
                    {"history": history, "preferences": preferences},
                )
                actual_action = intent.get("action")
                if actual_action != expected_intent_action:
                    passed = False
                    details.append(f"intent {actual_action} != {expected_intent_action}")

            if passed:
                totals["passed"] += 1
                self.stdout.write(f"[PASS] {scenario_id}")
            else:
                totals["failed"] += 1
                reason = "; ".join(details) if details else "mismatch"
                failures.append(f"{scenario_id}: {reason}")
                self.stdout.write(f"[FAIL] {scenario_id}: {reason}")

        self.stdout.write("")
        self.stdout.write(
            f"Total: {totals['total']} | Passed: {totals['passed']} | Failed: {totals['failed']} | Skipped: {totals['skipped']}"
        )
        if failures:
            self.stdout.write("Failures:")
            for failure in failures:
                self.stdout.write(f"- {failure}")
