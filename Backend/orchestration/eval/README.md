# Golden Eval Harness

This folder contains minimal golden scenarios to validate orchestration behavior.

Run the evaluator:

```bash
python Backend/manage.py run_golden_eval --allow-llm
```

Notes:
- `--allow-llm` enables LLM calls in planning/intent parsing.
- Without `--allow-llm`, the command will skip scenarios that require LLMs.
- Add scenarios to `golden_scenarios.json` as you capture real user logs.
