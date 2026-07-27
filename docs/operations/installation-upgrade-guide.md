# ByteBox Installation And Upgrade Guide

## Scope

This guide covers fresh ByteBox installs and the supported first migration from a legacy mem-store checkout or database.

## Fresh install

1. Install ByteBox into a virtual environment.

```powershell
.\.venv\Scripts\python.exe -m pip install -e .[dev]
```

2. Start from the sample configuration.

```powershell
Copy-Item bytebox.example.yaml .\bytebox.yaml
```

3. Adjust at least these values before first production use:

- `database.path`
- `application.state_dir`
- `security.ingest_roots`
- `api.auth` or `api.local_api_token`
- `embeddings.model_path` and offline settings when you do not allow runtime downloads

## Upgrade from mem-store

1. Migrate the legacy config in dry-run mode.

```powershell
bytebox config migrate .\config.yaml --dry-run
```

2. Write the migrated ByteBox config.

```powershell
bytebox config migrate .\config.yaml --out .\bytebox.yaml
```

The migration report renames `MEMORY_STORE_` keys and removes secret values from the output. Re-provision tokens, TLS key passwords, and similar secrets out-of-band.

3. Inspect the legacy database before cutover.

```powershell
bytebox database inspect --config .\bytebox.yaml --database-path .\data\memory_store
```

4. Plan the non-destructive migration.

```powershell
bytebox database migrate --config .\bytebox.yaml --source-database-path .\data\memory_store --target-database-path .\data\bytebox --backup-path .\backups\memory_store-pre-bytebox --dry-run --search-query "service layer"
```

5. Execute the migration only after the dry-run is clean.

```powershell
bytebox database migrate --config .\bytebox.yaml --source-database-path .\data\memory_store --target-database-path .\data\bytebox --backup-path .\backups\memory_store-pre-bytebox --overwrite --search-query "service layer"
```

6. If the embedding identity changed, preview and then run re-embedding.

```powershell
bytebox database reembed --config .\bytebox.yaml --database-path .\data\bytebox --dry-run
bytebox database reembed --config .\bytebox.yaml --database-path .\data\bytebox
```

7. Verify the migrated store before redirecting writes.

```powershell
bytebox database verify --config .\bytebox.yaml --database-path .\data\bytebox --search-query "service layer"
```

## Cutover checklist

- Stop the legacy writer before the final migration run.
- Keep the backup copy unchanged until rollback is no longer required.
- Validate record counts, schema version, graph links, and representative searches.
- Start ByteBox against the migrated target path only after verification succeeds.
- Remove the `memory_store` compatibility shim only according to the published release policy.