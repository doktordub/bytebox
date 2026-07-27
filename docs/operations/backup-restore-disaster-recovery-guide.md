# Backup, Restore, And Disaster Recovery Guide

## Routine inspection

```powershell
bytebox database inspect --config .\bytebox.yaml
```

Use the inspection output to confirm the path exists, the schema version is readable, the active vector dimension is known, and migration planning is clean before maintenance windows.

## Create a backup

```powershell
bytebox database backup --config .\bytebox.yaml --out .\backups\bytebox-20260726 --overwrite
```

The backup must be immutable for the duration of the release window.

## Verify a backup candidate

1. Restore the backup to an isolated path.
2. Run verification against the restored copy.

```powershell
bytebox database restore .\backups\bytebox-20260726 --database-path .\restore-check\bytebox --overwrite
bytebox database verify --config .\bytebox.yaml --database-path .\restore-check\bytebox --search-query "service layer"
```

## Recover from a failed migration or deployment

1. Stop ByteBox writes.
2. Restore the known-good backup into the target path.
3. Run `bytebox database verify` before reopening traffic.
4. Preserve the failed database copy for diagnosis.

## Lock handling

Use the unlock command only when the owning process is confirmed dead.

```powershell
bytebox unlock --database-path .\data\bytebox --force
```

## Recovery objectives

- prefer copy-based migration over in-place mutation;
- verify restored copies before production cutover;
- keep rollback backups until the release window closes;
- document the backup path used for each release candidate and GA tag.