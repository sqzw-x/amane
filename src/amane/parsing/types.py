"""番号解析的对外枚举; 单独成模块, 供解析规则与解析实现共用."""

from enum import StrEnum

__all__ = ["ContentType", "Mosaic"]


class ContentType(StrEnum):
    CENSORED = "censored"
    UNCENSORED = "uncensored"
    CHINESE = "chinese"
    WESTERN = "western"
    FC2 = "fc2"
    AMATEUR = "amateur"
    HENTAI = "hentai"


class Mosaic(StrEnum):
    CENSORED = "censored"
    UNCENSORED = "uncensored"
    CRACKED = "cracked"
    LEAKED = "leaked"
