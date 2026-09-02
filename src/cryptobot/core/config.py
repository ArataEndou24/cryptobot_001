"""設定。環境変数（.env）から読む。秘密情報はここ以外で扱わない。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    data_dir: Path = Field(default=Path("./data"), alias="CRYPTOBOT_DATA_DIR")
    state_dir: Path = Field(default=Path("./state"), alias="CRYPTOBOT_STATE_DIR")

    line_channel_access_token: str = Field(default="", alias="LINE_CHANNEL_ACCESS_TOKEN")
    line_target_user_id: str = Field(default="", alias="LINE_TARGET_USER_ID")
    line_monthly_budget: int = Field(default=150, alias="LINE_MONTHLY_BUDGET")


def load_settings() -> Settings:
    return Settings()
