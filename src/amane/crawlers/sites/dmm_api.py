"""DMM / Fanza TV GraphQL 响应模型. 只声明实际用到的字段."""

from pydantic import Field

from ..models import ResponseModel

# --- Fanza TV (tv.dmm.co.jp) ---

FANZA_TV_QUERY = """query FetchFanzaTvPlusContent($id: ID!, $device: Device!, $isForeign: Boolean) {
  fanzaTvPlus(device: $device) {
    content(id: $id, isForeign: $isForeign) {
      title
      description(format: HTML)
      packageImage
      packageLargeImage
      startDeliveryAt
      sampleMovie { url }
      samplePictures { imageLarge }
      actresses { name }
      directors { name }
      series { name }
      maker { name }
      label { name }
      genres { name }
      reviewSummary { averagePoint }
      playInfo { duration }
    }
  }
}"""


def fanza_tv_payload(cid: str) -> dict:
    return {
        "operationName": "FetchFanzaTvPlusContent",
        "variables": {
            "id": cid,
            "device": "BROWSER",
            "isForeign": False,
        },
        "query": FANZA_TV_QUERY,
    }


class _NameItem(ResponseModel):
    name: str = ""


class _SamplePicture(ResponseModel):
    imageLarge: str = ""


class _SampleMovie(ResponseModel):
    url: str = ""


class _ReviewSummary(ResponseModel):
    # 缺评分以 None 表示; 0.0 会被聚合当作真实评分.
    averagePoint: float | None = None


class _PlayInfo(ResponseModel):
    duration: int = 0


class FanzaTvContent(ResponseModel):
    title: str = ""
    description: str = ""
    packageImage: str = ""
    packageLargeImage: str = ""
    startDeliveryAt: str = ""
    # GraphQL 在无预览片时返回 null
    sampleMovie: _SampleMovie | None = None
    samplePictures: list[_SamplePicture] = Field(default_factory=list)
    actresses: list[_NameItem] = Field(default_factory=list)
    directors: list[_NameItem] = Field(default_factory=list)
    series: _NameItem | None = None
    maker: _NameItem | None = None
    label: _NameItem | None = None
    genres: list[_NameItem] = Field(default_factory=list)
    reviewSummary: _ReviewSummary | None = None
    # GraphQL 在无播放信息时返回 null
    playInfo: _PlayInfo | None = None


class _FanzaTvPlus(ResponseModel):
    # GraphQL 在内容不存在时返回 null
    content: FanzaTvContent | None = None


class _FanzaData(ResponseModel):
    # GraphQL 在内容不存在时返回 null
    fanzaTvPlus: _FanzaTvPlus | None = None


class FanzaTvResponse(ResponseModel):
    data: _FanzaData = Field(default_factory=_FanzaData)


# --- DMM TV (tv.dmm.com) ---

DMM_TV_QUERY = """query FetchVideo($seasonId: ID!, $device: Device!) {
  video(id: $seasonId) {
    titleName
    description(format: HTML)
    packageImage
    keyVisualImage
    productionYear
    startPublicAt
    casts { actorName }
    staffs { roleName staffName }
    genres { name }
    ... on VideoSeason {
      reviewSummary { averagePoint }
    }
    ... on VideoLegacySeason {
      reviewSummary { averagePoint }
    }
  }
}"""


def dmm_tv_payload(season_id: str) -> dict:
    return {
        "operationName": "FetchVideo",
        "variables": {
            "seasonId": season_id,
            "device": "BROWSER",
        },
        "query": DMM_TV_QUERY,
    }


class _Cast(ResponseModel):
    actorName: str = ""


class _Staff(ResponseModel):
    roleName: str = ""
    staffName: str = ""


class DmmTvVideo(ResponseModel):
    titleName: str = ""
    description: str = ""
    packageImage: str = ""
    keyVisualImage: str = ""
    productionYear: int = 0
    startPublicAt: str = ""
    casts: list[_Cast] = Field(default_factory=list)
    staffs: list[_Staff] = Field(default_factory=list)
    genres: list[_NameItem] = Field(default_factory=list)
    # GraphQL 在无评分时返回 null
    reviewSummary: _ReviewSummary | None = None


class _DmmTvData(ResponseModel):
    # GraphQL 在内容不存在时返回 null
    video: DmmTvVideo | None = None


class DmmTvResponse(ResponseModel):
    data: _DmmTvData = Field(default_factory=_DmmTvData)


# --- Digital (video.dmm.co.jp) ---

DIGITAL_QUERY = """query FetchDigitalContent($id: ID!) {
  ppvContent(id: $id) {
    title
    description
    deliveryStartDate
    duration
    actresses { name }
    directors { name }
    series { name }
    maker { name }
    label { name }
    genres { name }
    packageImage { largeUrl mediumUrl }
    sampleImages { imageUrl largeImageUrl }
    sample2DMovie { highestMovieUrl }
  }
  reviewSummary(contentId: $id) {
    average
  }
}"""


def digital_payload(cid: str) -> dict:
    return {
        "operationName": "FetchDigitalContent",
        "variables": {"id": cid},
        "query": DIGITAL_QUERY,
    }


class _DigitalPackageImage(ResponseModel):
    # 素人条目只给 mediumUrl (jp.jpg), largeUrl 为 null; 两个字段都可能单独缺省.
    largeUrl: str | None = None
    mediumUrl: str | None = None


class _DigitalSampleImage(ResponseModel):
    imageUrl: str = ""
    largeImageUrl: str = ""


class _DigitalSampleMovie(ResponseModel):
    highestMovieUrl: str = ""


class _DigitalReviewSummary(ResponseModel):
    # 缺评分以 None 表示; 0.0 会被聚合当作真实评分.
    average: float | None = None


class DigitalContent(ResponseModel):
    title: str = ""
    description: str = ""
    deliveryStartDate: str = ""
    duration: int = 0
    actresses: list[_NameItem] = Field(default_factory=list)
    directors: list[_NameItem] = Field(default_factory=list)
    series: _NameItem | None = None
    maker: _NameItem | None = None
    label: _NameItem | None = None
    genres: list[_NameItem] = Field(default_factory=list)
    # GraphQL 在无封面图时返回 null
    packageImage: _DigitalPackageImage | None = None
    sampleImages: list[_DigitalSampleImage] = Field(default_factory=list)
    # GraphQL 在无预览片时返回 null
    sample2DMovie: _DigitalSampleMovie | None = None


class _DigitalData(ResponseModel):
    # GraphQL 在内容不存在时返回 null
    ppvContent: DigitalContent | None = None
    # GraphQL 在无评分时返回 null
    reviewSummary: _DigitalReviewSummary | None = None


class DigitalResponse(ResponseModel):
    data: _DigitalData = Field(default_factory=_DigitalData)
