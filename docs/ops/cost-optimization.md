# Cost & Compute Guardrails (Feb 4, 2026)

## Implemented now
- **Token caps:** Hard cap `LLM_MAX_TOKENS` (default 700) applied to all Anthropic/HF calls.
- **Prompt truncation:** User/system prompts truncated to `LLM_PROMPT_CHAR_LIMIT` (default 4k chars) to avoid runaway context.
- **Streaming-friendly defaults:** Lower default `max_tokens` (600) across generate/stream.
- **Trial ops batching:** Daily email summary is a single batch to superusers + fallback inbox; no AI calls.
- **Trial expiry enforcement:** Middleware downgrades expired trials without extra jobs.

## Recommended next (no code yet)
1) **Priority queues & autoscale:** Separate Celery queues: `realtime` (chat), `batch` (emails), `heavy` (long workflows); scale workers on queue depth/time-in-queue. Run beat as platform cron to allow scaling workers to zero off-peak.
2) **RAG/context trimming:** Cap retrieval to top-3 chunks; hard cap combined context to ~1.5k tokens; dedupe sources.
3) **Token accounting:** Log tokens in/out per route; alert on outliers. Use “concise mode” flag for mobile to request shorter outputs.
4) **Cache deterministic answers:** Short-TTL cache for FAQ/onboarding/prompts that repeat; memoize common summaries per user/day.
5) **Concurrency guards:** Add per-room/day soft limits on AI calls; require confirmation for fan-out (multi-post) actions.
6) **Front-end weight:** Enable permessage-deflate on WebSockets; lazy-load heavy JS; inline critical CSS on chat shell for faster TTFB on small droplets.

## Expected scale
- ~100 DAU fits on 1–2 vCPU app + 1 vCPU worker + small Redis if token caps + truncation are enforced.
- Keep Temporal/DB/Redis in the same region to reduce latency (and token padding).
