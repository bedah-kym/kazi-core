# Skills

A **skill** is a drop-in instruction pack — a folder with a `SKILL.md` that
teaches the agent one thing well. No database, no new dependencies.

## How it works

The registry scans the `skills/` directory on boot. Each skill is a folder
containing a `SKILL.md` with a small frontmatter block:

```yaml
---
name: web-scraper
description: Extract structured data from a public webpage
tools: search_info
stage: active        # staging | review | active
---
```

Only skills marked `active` are exposed to the agent. The others stay parked.

## Promotion gates

Skills move through three stages:

- `staging` — draft, invisible to the agent
- `review` — visible to operators, not yet live
- `active` — loaded and usable

Flip the `stage:` line to promote a skill. There's a `SKILL_MAX_CHARS` cap
(default `8000`) on the instruction body so one skill can't eat the context.

## Using skills

The agent can call the `list_skills` and `load_skill` meta-tools to discover
and load a skill mid-conversation — you don't have to restart to use one.

## Shipped example

`skills/report-formatting/` shows the full shape: a `SKILL.md` with frontmatter
and instructions.

## Next

- [Configuration](configuration.md) — `SKILL_MAX_CHARS` and friends
- [Features](features.md) — where Skills fit in the bigger picture
