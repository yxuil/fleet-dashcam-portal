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

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "dashcam-clips"
    s3_region: str = "us-east-1"


settings = Settings()
