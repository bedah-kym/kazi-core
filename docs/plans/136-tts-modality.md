# Plan: #136 TTS generation connector + delivery flag + modality hint

Status: draft — awaiting human approval (protected path)

## Goal

"Sing the baby a lullaby" resolves to a voice note on a voice-capable channel,
with graceful text degradation otherwise. Modality is a connector + hint +
channel flags — not a pipeline stage (brief §4.2).

## Non-goals

- No modality-selection pipeline stage.
- No real provider call in tests (mocked audio).
- No shell involvement.

## Touches

- Protected: `Backend/orchestration/action_catalog.py` — a static
  `generate_speech` entry so it is always advertised (`workflows.capabilities`
  builds its catalog at import; a discovered-only entry is not guaranteed to be
  present there).
- `Backend/orchestration/connectors/tts_connector.py` (new): `generate_speech`
  (`text`, optional `voice`, `format`); key via `self.get_credential(...)`.
- `Backend/notifications/capabilities.py` (new): per-channel
  `supports_voice`/`supports_image`/`max_audio_bytes` + `plan_voice_delivery`
  (text+note fallback).
- `Backend/orchestration/agent_prompts.py`: one modality hint.
- `Backend/orchestration/test_v06_metrics.py`: remove `@expectedFailure` from
  the lullaby metric (it is shipped by this PR).
- Tests: `test_tts_connector.py`, `test_notifications_capabilities.py`.
- Protected paths? yes: `action_catalog.py`.

## Risk class

read-only/connector: generating audio has no external world effect beyond a
provider call the user asked for; `risk_level: low`.

## Approach

1. `tts_connector` posts to a configurable TTS endpoint (`OPENAI_TTS_URL`,
   key `OPENAI_API_KEY`) with httpx; returns base64 audio + byte count + format.
   Missing key -> normalized error ("voice is not configured").
2. `notifications.capabilities`: a small channel table; `plan_voice_delivery`
   returns `{mode: "voice"|"text", note}` — a non-voice channel yields text + a
   note instead of failing.
3. `agent_prompts`: append `_MODALITY_RULES` ("when the user asks you to
   sing/speak/play, prefer a voice tool").
4. Static `action_catalog` entry (`service: voice`, `risk_level: low`).

## Verification (executable)

- `test_v06_metrics` lullaby metric passes (decorator removed).
- `test_tts_connector`: mocked provider success; missing key error; provider
  failure normalized.
- `test_notifications_capabilities`: voice-capable vs text-only channel
  degradation.
- Full suite + flake8 + bandit + `check_boundaries.py`.

## Rollback

Revert; remove the catalog entry and connector.

## Open questions for the human

- TTS provider: default to an OpenAI-compatible `/audio/speech` endpoint behind
  `OPENAI_TTS_URL`/`OPENAI_API_KEY`, or a different provider? Recommend the
  OpenAI-compatible shape (already an installed SDK/codepath).
- Confirm the lullaby metric decorator is removed here (it would otherwise
  report "unexpected success" and fail CI).
