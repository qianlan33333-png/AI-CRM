from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DynamicMiniprogramCardV1(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal["dynamic_miniprogram_card.v1"] = "dynamic_miniprogram_card.v1"
    appid: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=64)
    pagepath: str = Field(min_length=1, max_length=1024)
    card_id: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    cid: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")
    cover_image_id: int = Field(gt=0)

    @field_validator("title")
    @classmethod
    def title_fits_wecom_bytes(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 64:
            raise ValueError("title exceeds 64 UTF-8 bytes")
        return value


__all__ = ["DynamicMiniprogramCardV1"]
