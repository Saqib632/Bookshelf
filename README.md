# Chat With Your Book

Architecture-first MVP for an authenticated document chat application using RAG with a corrective retrieval loop (CRAG).

## Current Status

Phase 1 is complete as a design proposal. Implementation is intentionally blocked until the architecture is reviewed and approved.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for:

- technology decisions and tradeoffs
- component, ingestion, and CRAG flow diagrams
- MongoDB collections and indexes
- REST API contract
- proposed frontend/backend folder structure
- security and prompt-injection defenses
- local and production deployment plan

## Run locally

1. Copy `backend/.env.example` to `backend/.env` and set `SECRET_KEY`.
2. Start infrastructure with `docker compose up -d mongo redis qdrant`.
3. From `backend`, install with `python -m pip install -e .` and start the API with `uvicorn app.main:app --reload --port 8000`.
4. In a second `backend` terminal, start the worker with `dramatiq app.workers.tasks`.
5. In `frontend`, run `npm install` once, then `npm run dev` and open `http://localhost:3000`.

Uploads are streamed to the backend's controlled temporary directory and deleted after processing. No external object storage is used. The default deterministic embedding and grounded fallback providers make local pipeline tests deterministic; configure production model provider implementations before deployment.

## Non-negotiable constraints

- MongoDB is the system of record.
- Original files exist only in the backend's temporary processing directory and are deleted after processing.
- Qdrant owns embeddings and vector search.
- All ownership is derived from the authenticated token server-side.
- Document text is untrusted evidence, never instructions.
- Answers without sufficient book evidence explicitly say the information was not found in the uploaded book.
