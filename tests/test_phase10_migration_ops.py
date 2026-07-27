from __future__ import annotations

from pathlib import Path

from bytebox.database_ops import derive_target_database_path


def test_derive_target_database_path_prefers_bytebox_names() -> None:
    assert derive_target_database_path(Path("data") / "memory_store").name == "bytebox"
    assert derive_target_database_path(Path("data") / "mem-store").name == "bytebox"
    assert derive_target_database_path(Path("data") / "arcade").name == "arcade-bytebox"