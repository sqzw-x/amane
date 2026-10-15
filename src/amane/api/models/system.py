from pydantic import BaseModel


class DesktopResponse(BaseModel):
    version: str
    data_dir: str
    supervised: bool = False


class ReleaseResponse(BaseModel):
    current: str
    latest: str | None
    html_url: str | None
    newer: bool
