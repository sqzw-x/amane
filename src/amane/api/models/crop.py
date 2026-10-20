from pydantic import BaseModel, Field, model_validator

from ...media.images import CropRotation


class CropBoxRequest(BaseModel):
    """像素框 (右 / 下为开区间) 与顺时针旋转角度; 坐标相对 EXIF 转正并旋转后的图像."""

    left: int = Field(ge=0, description="裁切框左边界 (含)")
    top: int = Field(ge=0, description="裁切框上边界 (含)")
    right: int = Field(gt=0, description="裁切框右边界 (不含)")
    bottom: int = Field(gt=0, description="裁切框下边界 (不含)")
    rotation: CropRotation = Field(
        default=0,
        description="裁切前顺时针旋转的角度 (仅 90° 整数倍); 90° / 270° 后宽高互换",
    )

    @model_validator(mode="after")
    def _box_positive_area(self) -> CropBoxRequest:
        if self.left >= self.right or self.top >= self.bottom:
            raise ValueError("裁切区域须为正矩形 (left < right, top < bottom)")
        return self
