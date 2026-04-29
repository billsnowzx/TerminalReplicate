from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    streamlit_server_port: int = 8501
    data_dir: Path = Field(default=Path("./data"))
    database_url: str = "sqlite:///./data/macro_platform.db"
    raw_snapshot_dir: Path = Field(default=Path("./data/raw"))
    report_output_dir: Path = Field(default=Path("./data/reports"))
    report_script_dir: Path = Field(default=Path("./data/report_scripts"))
    notification_output_dir: Path = Field(default=Path("./data/notifications"))
    enable_report_scheduler: bool = False
    report_scheduler_poll_seconds: int = 300
    fred_api_key: str | None = None
    bls_api_key: str | None = None
    use_openbb_market_provider: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def ensure_paths(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.raw_snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.report_output_dir.mkdir(parents=True, exist_ok=True)
        self.report_script_dir.mkdir(parents=True, exist_ok=True)
        self.notification_output_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_paths()
