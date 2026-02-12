## Mathia Platform – Context-Aware Intelligence Roadmap

This plan extends Mathia from “LLM + tools” to a business-aware assistant with reliable behavior, while keeping infra light for a solo dev. It is organized into phases you can implement incrementally.

### Goals
- Keep Mathia on cheap/managed infra (no self-hosting GPUs).
- Make answers business-aware via retrieval (RAG) and, later, light fine-tuning.
- Preserve existing orchestration (Django + Channels + Temporal + connectors).
- Keep safety/consistency: persona, tool constraints, and guardrails.

### Current Architecture (baseline)
- Frontend: Django templates + Channels WebSockets; intent classification in `orchestration/intent_parser.py`; tool routing via `mcp_router.py`; workflows via Temporal.
- Connectors: Mailgun/WhatsApp/Payments/Travel etc.
- State: Postgres + Redis; vector store not yet present.

---
## Phase 1 — Retrieval Layer (RAG) [Lowest cost, highest immediate value]
**Objective:** Add a business “memory” so Mathia answers with your docs, without model changes.

1) **Data sources**
   - Project docs (`docs/`, `workflow_implementation_doc.md`, `STRESS_TEST.md`, README changes).
   - Business FAQs / policies (add a `docs/business/` folder).
   - Optional: export selected Notion pages to Markdown when ready.

2) **Ingestion pipeline**
   - CLI management command: `python Backend/manage.py ingest_docs --path docs/ --reset`
   - Steps: crawl markdown/txt/pdf → chunk (500-800 tokens with overlap) → embed → store vectors.
   - Embeddings: start with OpenAI `text-embedding-3-small` or HF `all-MiniLM-L6-v2` (via `sentence-transformers`); choose based on token cost vs. on-prem needs.

3) **Vector store**
   - Easiest: SQLite + FAISS (`faiss.IndexFlatIP`) stored under `var/vectorstore/`.
   - If you prefer Postgres: add `pgvector` extension on `mathia-project-db-1` and store in a `documents` table. (FAISS is simpler to start.)

4) **Retrieval step**
   - Add a helper `orchestration/rag.py`:
     - `retrieve(query, k=5, filters=None)` returns top chunks + metadata.
     - `build_context(chunks)` formats context with source tags.
   - Plug into the LLM client wrapper: before calling Claude/HF, fetch context and prepend to system prompt for `general_chat`, `search_info`, and tool-responses where relevant.
   - Cache per-query embeddings in Redis with a short TTL to cut latency.

5) **Prompt updates**
   - System prompt: Add persona (“Mathia”), tool constraints, and “Ground answers in provided context; if insufficient, say you don’t know.”
   - Include a short “Sources” section in the message to the user when possible (links or doc titles).

6) **Safety/guardrails**
   - Keep existing intent parsing; if retrieval returns low similarity, fall back to general_chat with a clarification question.
   - Keep tool whitelist in `capabilities.py`; RAG should not invent tools.

7) **Metrics & logs**
   - Log retrieval hits/misses and latency to a small table or JSONL in `var/logs/rag.log`.

---
## Phase 2 — Tone & Behavior Fine-Tuning (hosted APIs)
**Objective:** Make Mathia speak consistently in your brand voice and handle edge cases gracefully.

1) **Dataset curation**
   - Collect 200–1,000 chat snippets: user → Mathia, covering support, workflows, travel, payments, “I don’t know” cases, and safe refusals.
   - Store as JSONL (`{"input": "...", "output": "..."}`) in `data/finetune/`.

2) **Fine-tune target**
   - Start with OpenAI GPT-4o mini or GPT-3.5; Anthropic when fine-tune is available.
   - Keep fine-tune small; rely on RAG for facts.

3) **Integration**
   - Add a flag `USE_FINETUNED_MODEL=true` in `.env`.
   - Update LLM client to pick the FT model for `general_chat` and intent parsing; keep base model for tool-heavy calls if cost/latency matters.

4) **Safety**
   - Include refusal examples and “ask for clarification” examples in the dataset.
   - Run a red-team checklist (prompts in `STRESS_TEST.md` security section) against the FT model before enabling in prod.

---
## Phase 3 — Domain Adapter (optional, more control)
**Objective:** If API costs rise or you need offline control, add a LoRA adapter on a small model.

1) **Model choice**
   - Llama-3.1-8B-Instruct or Mistral 7B.
   - Train LoRA with the same JSONL dataset; use 4–8 A100 hours on a cloud trainer (Modal/RunPod).

2) **Serving**
   - Avoid self-hosting; use Together/Replicate/HF Inference Endpoint with your adapter.
   - Keep RAG layer identical; swap endpoint URL.

---
## Phase 4 — Productization & Ops
1) **Feature flags**
   - `.env`: `RAG_ENABLED`, `USE_FINETUNED_MODEL`, `VECTOR_BACKEND=faiss|pgvector`.
2) **Background jobs**
   - Celery beat task to re-ingest docs daily/weekly.
   - Health checks: RAG availability, embedding latency, and vector store size.
3) **Cost controls**
   - Cap embedding batch size; deduplicate by checksum before embedding.
   - Token budgeting per message; truncate history when RAG context is present.

---
## Work Packages (Dev Tasks)
1) **RAG scaffold**
   - Add `orchestration/rag.py` (retrieve, build_context, embed helper).
   - Add management command `ingest_docs`.
   - Wire retrieval into `mcp_router` or LLM client wrapper for chat flows.
2) **Storage**
   - Create `var/vectorstore/` and add to `.gitignore`.
   - Optional: pgvector migration and seed script.
3) **Prompts**
   - Update system prompt templates to include persona, tools, and “use provided context.”
4) **Logging/metrics**
   - Add basic counters: retrieval_hits, retrieval_misses, rag_latency_ms.
5) **Fine-tune prep (later)**
   - Add `data/finetune/README.md` with dataset format and a small seed set.

---
## Success Criteria
- Chat answers cite internal docs and avoid “I don’t know” on known policies.
- Intent routing unchanged; tool use remains safe.
- Latency impact of RAG < 400 ms p95 on typical queries.
- Easy rollback via env flags.

---
## Biz Value Snapshot
- Better onboarding/support: answers match your processes without manual replying.
- Safer automation: workflows and connectors are used with your guardrails.
- Lower ops cost than self-hosting models; FT optional and incremental.

---
## Next Steps (suggested order)
1) Implement Phase 1 (RAG) with FAISS + CLI ingest; ship behind `RAG_ENABLED`.
2) Collect 200 example Q&A pairs while observing chats; clean and store in `data/finetune/`.
3) Decide on fine-tune vs. stick with RAG; turn on when ready.
