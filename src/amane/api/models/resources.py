from pydantic import BaseModel, Field


class ResourceUploadResponse(BaseModel):
    """上传后的资源引用; 内容相同的重复上传返回同一个 URL."""

    url: str = Field(description="内部资源 URL (/api/resources/{hash}), 可直接写入影片的图片字段")
