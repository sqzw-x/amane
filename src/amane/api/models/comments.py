from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

# 正文先去除首尾空白再校验长度: 全空白与超长都在入库前拒绝, 不由路由层补 strip.
CommentBody = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10000)]


class CommentResponse(BaseModel):
    id: int
    metadata_id: int
    body: str
    created_at: datetime
    updated_at: datetime


class CommentCreateRequest(BaseModel):
    body: CommentBody


class CommentUpdateRequest(BaseModel):
    body: CommentBody
