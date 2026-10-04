from pydantic import BaseModel, Field, model_validator


class CropBoxRequest(BaseModel):
    """像素框 (右 / 下为开区间); 坐标基准由具体端点约定."""

    left: int = Field(ge=0, description="裁切框左边界 (含)")
    top: int = Field(ge=0, description="裁切框上边界 (含)")
    right: int = Field(gt=0, description="裁切框右边界 (不含)")
    bottom: int = Field(gt=0, description="裁切框下边界 (不含)")

    @model_validator(mode="after")
    def _box_positive_area(self) -> CropBoxRequest:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("裁切区域须为正矩形 (left < right, top < bottom)")
        return self
