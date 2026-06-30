from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "dev"

    database_url: str = "postgresql+asyncpg://dashcam:dashcam@localhost:5432/dashcam"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"

    # Storage backend selection.
    # - ``local`` (default): clip MP4s live on disk under ``storage_root``;
    #   playback is served by ``GET /clips/{id}/stream`` via FileResponse.
    # - ``s3``: clip MP4s live in MinIO/S3; playback is a SigV4 presigned
    #   GET URL minted on demand.
    storage_backend: Literal["local", "s3"] = "local"

    # Filesystem root for local-mode clip storage. Resolved relative to the
    # backend process's cwd. Created on demand by ``ensure_bucket`` in local
    # mode; ignored in s3 mode.
    storage_root: Path = Path("./var/clips")

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "dashcam-clips"
    s3_region: str = "us-east-1"

    # Maximum allowed size, in bytes, for an upload through
    # ``POST /clips/upload``. Default 1 GiB — large enough for a few minutes
    # of dashcam footage, small enough that a single multipart request
    # doesn't tie up the worker indefinitely. Exceeding this returns
    # ``413 Payload Too Large`` without leaving partial bytes on disk.
    max_upload_bytes: int = 1_073_741_824


settings = Settings()
