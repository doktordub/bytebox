# ByteBox Phase 0 Provenance And License Review

Snapshot date: 2026-07-26

## Review Goal

Establish whether the current `bytebox` workspace has a clear enough provenance record to continue the ByteBox rebrand and what must happen before public production artifacts are published.

## Evidence Reviewed

1. Current workspace metadata and repository structure.
2. User-provided provenance statement: current code was cloned from `doktordub/mem-store`.
3. Current repository attachment metadata: repository name `bytebox`, owner `doktordub`, branch `main`.
4. Public upstream repository page for `https://github.com/doktordub/mem-store`.
5. Current workspace root file inventory.
6. Configured default model pages on Hugging Face.

## Repository Provenance Findings

| Topic | Finding | Result |
|---|---|---|
| Source repository | The workspace is explicitly described as cloned from `doktordub/mem-store` | verified by user statement and matching repository contents |
| Current repository owner | The current repository metadata identifies owner `doktordub` | verified |
| Upstream owner | The upstream public repository is also owned by `doktordub` | verified |
| Contributor footprint | The upstream page shows one contributor | verified |
| Workspace `LICENSE` file | No root `LICENSE*` file exists in the current workspace | missing |
| Workspace `NOTICE` file | No root `NOTICE*` file exists in the current workspace | missing |
| Upstream visible license file | The scraped upstream repository root did not surface a visible root license file in the reviewed listing | unresolved for open-source redistribution |
| Release artifacts | Upstream public page shows no releases | verified |

## Code-Copy Right Assessment

The current evidence supports a narrow conclusion:

- continuing the ByteBox refactor inside the same apparent owner namespace (`doktordub`) is plausible;
- the repository provenance is not ambiguous in the sense of "unknown source code";
- however, the repository tree still lacks an explicit root license and notice record, which is not acceptable for a public production release.

Phase 0 therefore treats repository provenance as sufficient to continue owner-controlled refactor work, but not sufficient to publish ByteBox as a well-documented redistributable artifact until Phase 1 adds explicit licensing and attribution files.

## Model License Findings

Configured defaults and observed runtime artifacts:

| Artifact | Source | License status |
|---|---|---|
| `BAAI/bge-small-en-v1.5` | Hugging Face model page | `MIT` |
| `Qdrant/bge-small-en-v1.5-onnx-Q` | Hugging Face model page; observed FastEmbed runtime artifact | `Apache-2.0` |
| `cross-encoder/ms-marco-MiniLM-L6-v2` | Hugging Face model page | `Apache-2.0` |
| `Xenova/ms-marco-MiniLM-L-6-v2` | Hugging Face alias page used in current config | alias page reviewed; explicit license not surfaced in the first scrape, treat upstream `cross-encoder` model license as the authoritative fallback until ByteBox records the exact artifact provenance |

What is still missing from the repository itself:

- a checked-in model-license inventory;
- explicit attribution or notice text for shipped model artifacts;
- a statement of whether ByteBox will distribute model binaries or require local provisioning.

## Legal Gaps That Must Be Closed Before Public Release

1. Add a root `LICENSE` file for ByteBox.
2. Add `NOTICE` / attribution files if the final distribution path requires them.
3. Record model artifact provenance and licenses in repository documentation.
4. Decide whether ByteBox will ever publish model binaries or only consume user-provisioned local artifacts.
5. Document the transition policy from `memory-store` to ByteBox in release notes and migration docs.

## Gate Result

Phase 0 provenance review is complete.

- Status for continuing refactor work: acceptable within the current owner-controlled repository lineage.
- Status for public production release: blocked until ByteBox adds explicit license, notice, and model-attribution records.