"""项目制品存储服务.

Phase 1: 本地文件系统存储 (默认)
Phase 2: 支持 S3/MinIO 对象存储
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)


class StorageBackend(Protocol):
    async def write(self, prefix: str, path: str, content: str) -> str:
        ...

    async def read(self, prefix: str, path: str) -> str:
        ...

    async def list_files(self, prefix: str) -> list[str]:
        ...

    async def create_zip(self, prefix: str, output_path: str) -> str:
        ...


class LocalStorageBackend:
    """本地文件系统存储, 默认 prefix 在 generated-projects/ 下."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.generated_projects_dir or "generated-projects")
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, prefix: str, path: str) -> Path:
        # 防止路径遍历
        safe_prefix = prefix.replace("..", "").strip("/")
        safe_path = path.replace("..", "").lstrip("/")
        return self.base_dir / safe_prefix / safe_path

    async def write(self, prefix: str, path: str, content: str) -> str:
        target = self._resolve(prefix, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        log.debug("storage_write", path=str(target))
        return str(target)

    async def read(self, prefix: str, path: str) -> str:
        target = self._resolve(prefix, path)
        return target.read_text(encoding="utf-8")

    async def list_files(self, prefix: str) -> list[str]:
        root = self.base_dir / prefix.replace("..", "").strip("/")
        if not root.exists():
            return []
        files = []
        for p in root.rglob("*"):
            if p.is_file():
                files.append(str(p.relative_to(root)).replace("\\", "/"))
        return files

    async def create_zip(self, prefix: str, output_path: str) -> str:
        root = self._resolve(prefix, "")
        zip_path = self.base_dir / output_path
        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in root.rglob("*"):
                if p.is_file():
                    arcname = str(p.relative_to(root)).replace("\\", "/")
                    zf.write(p, arcname)
        return str(zip_path)


# 简单工厂
def get_storage_backend() -> StorageBackend:
    return LocalStorageBackend()
