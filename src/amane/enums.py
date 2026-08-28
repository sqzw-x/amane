from enum import StrEnum


class SiteName(StrEnum):
    """爬虫站点名称 (影片与演员源共用)."""

    AIRAV = "airav"
    AVSOX = "avsox"
    DAHLIA = "dahlia"
    DMM = "dmm"
    FALENO = "faleno"
    FC2 = "fc2"
    FC2CLUB = "fc2club"
    FC2PPVDB = "fc2ppvdb"
    FREEJAVBT = "freejavbt"
    GETCHU = "getchu"
    GFRIENDS = "gfriends"
    GIGA = "giga"
    IQQTV = "iqqtv"
    JAV321 = "jav321"
    JAVBUS = "javbus"
    JAVDB = "javdb"
    JAVLIBRARY = "javlibrary"
    KIN8 = "kin8"
    MGSTAGE = "mgstage"
    MINNANO = "minnano"
    OFFICIAL = "official"
    PRESTIGE = "prestige"
    R18DEV = "r18dev"
    THEPORNDB = "theporndb"
    WIKIPEDIA = "wikipedia"
    XCITY = "xcity"


class Language(StrEnum):
    """支持的语言代码."""

    ZH_CN = "zh_cn"
    ZH_TW = "zh_tw"
    JP = "jp"
    EN = "en"


class ActorGender(StrEnum):
    """演员性别 - 用于展示与按站裁剪刮削源."""

    FEMALE = "female"
    MALE = "male"
    UNKNOWN = "unknown"


class MoveMode(StrEnum):
    """整理时把源文件放到模板路径的方式."""

    MOVE = "move"
    COPY = "copy"
    HARDLINK = "hardlink"
    SYMLINK = "symlink"


class LinkMode(StrEnum):
    """整理后在 link_template 位置如何指向真实视频."""

    STRM = "strm"
    SYMLINK = "symlink"


class LibraryAutomation(StrEnum):
    """媒体库自动化级别. 含更低级别的行为; 自动整理尚未开放."""

    NONE = "none"
    WATCH = "watch"
    SCRAPE = "scrape"


class DownloadableResource(StrEnum):
    """影片附属资源类型: 刮削进 Resource, 整理时按库配置复制到库路径."""

    thumb = "thumb"
    poster = "poster"
    extrafanart = "extrafanart"
    trailer = "trailer"


class MetadataField(StrEnum):
    """所有可配置优先级的元数据字段."""

    TITLE = "title"
    PLOT = "plot"
    ACTORS = "actors"
    DIRECTORS = "directors"
    TAGS = "tags"
    SERIES = "series"
    RELEASE = "release"
    RUNTIME = "runtime"
    PUBLISHER = "publisher"
    STUDIO = "studio"
    POSTER_URLS = "poster_urls"
    THUMB_URLS = "thumb_urls"
    TRAILER_URLS = "trailer_urls"
    EXTRAFANART = "extrafanart"
    SCORE = "score"
