# RAG Diagnosis and Validation

## Confirmed root causes

1. `GroundedFallbackLLM` was always selected. It returned the first retrieved chunk with a fixed 700-character slice, which explains the repeated `Based on the uploaded book:` prefix and mid-sentence truncation. No answer-generation LLM was being called.
2. `DeterministicEmbeddingProvider` hashes text with SHA-256. The vectors are not semantic, so typos and paraphrases cannot retrieve meaningfully related passages. Existing vectors must be re-indexed after configuring a real embedding model.
3. Vector hits with zero lexical overlap were discarded by `search_chunks`, so semantic retrieval could not rescue title-page or typo cases.
4. The browser did not preserve `conversation_id`, so follow-up questions always started a new conversation.
5. PDF extraction has no OCR fallback and the upload contract currently accepts only PDF/DOCX; image uploads are not indexed.

## Required configuration

The local default uses `sentence-transformers` with `all-MiniLM-L6-v2`, so no embedding API key is required. For a hosted provider, set `EMBEDDING_PROVIDER=remote`, `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, and `EMBEDDING_BASE_URL` to a provider compatible with `/v1/embeddings`, then reprocess every existing book. Index and query vectors must use the same model, dimensions, distance metric, normalization, and provider-specific prefixes.

Re-embed retained MongoDB chunks with:

```powershell
Set-Location backend
python -m app.reindex
```

The command writes to `book_chunks_semantic`, which keeps old 32-dimensional digest vectors isolated.

The answer provider uses `/v1/chat/completions`. Keep `LLM_API_KEY`, `LLM_MODEL`, and `LLM_BASE_URL` together. The local example uses Groq's OpenAI-compatible endpoint.

## Quick retrieval checks

Run five materially different questions and log each result's `chunk_id`, page, score, and first 200 characters. Then compare query vectors with cosine similarity. Query vectors should differ substantially; a near-1.0 matrix or all-zero vectors indicates a broken provider. Sample queries for the thesis:

```text
what is the project's purpose?
who is Dr. Waheed?
which technologies are used?
what are the limitations?
what hardware is required?
```

Also sample stored chunk-vector cosine similarities. A distribution concentrated at 1.0 indicates duplicate or degenerate vectors. Save and inspect the exact `SYSTEM_PROMPT` plus `build_answer_prompt(...)` output for one request.

## Regression plan

For the thesis, assert retrieval hit@5 for sources covering: supervisor, purpose/objective, technologies, limitations, hardware requirements, team members, functional requirements, architecture, testing, and conclusion. Separately grade each generated answer for: direct response to the question, no unsupported claims, correct page/section citations, and no verbatim chunk dump. Include typo variants such as `voive assistant` and an out-of-scope question that must return the not-found sentence.

The minimum release gate is: no fixed fallback prefix, different questions produce different final prompts and answers, vector-only candidates remain eligible, and all cited chunks belong to the requested user's book scope.