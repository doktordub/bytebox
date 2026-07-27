# Module Size Policy

ByteBox phase 2 keeps new core modules small enough to review in one pass.

- Default limit: new `application`, `domain`, `services`, and CLI modules should stay at or below 400 lines.
- Reviewed exception limit: compatibility or orchestration facades may extend to 650 lines when they only preserve the public surface while later phases keep decomposing runtime ownership.
- Exception reviews must be explicit in this file and removed once the owning phase finishes the deeper extraction.

## Current Exceptions

- `src/bytebox/service.py` — compatibility facade plus shared runtime helpers until Phase 3 moves startup/shutdown ownership into a dedicated lifespan container.
- `src/bytebox/services/ingestion_document.py` — transitional document-ingestion workflow while later phases move chunking, persistence, and recovery collaborators deeper behind dedicated ports.
- `src/bytebox/services/ingestion_folder.py` — transitional folder-ingestion orchestration while manifest state and recovery rules still live together during the phase-2 compatibility cut.