from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WG_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./wireguard.db"
    bootstrap_api_key: str = ""
    bootstrap_tenant_id: str = "local"
    config_encryption_key: str = ""
    agent_token: str = ""
    agent_state_key: str = ""
    agent_interface: str = "wg0"
    agent_listen_host: str = "127.0.0.1"
    agent_listen_port: int = 8787
    agent_state_path: str = "/var/lib/wg-agent/agent.sqlite3"
    worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)


settings = Settings()
