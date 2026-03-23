from __future__ import annotations

from pydantic import BaseModel, Field


class OpenEnvAdapterConfig(BaseModel):
    base_url: str = Field(default="http://127.0.0.1:8011")
    timeout: float = Field(default=10.0, gt=0)
    env_name: str = Field(default="demo_env")
    