"""DMM 图片 CDN 的域名与路径规则, 由 DMM 详情页与 r18 dump 两个来源共用.

两个 CDN 存放同一份文件, 差别只在路径: pics 的 mono 地址在 cid 前多一层被忽略的分类目录
(adult / amateur / japanese), aws 只接受省去该层的规范路径; 其余分类 (e-book 等) 没有副本.
"""

PICS_CDN = "https://pics.dmm.co.jp"
HIGH_RES_CDN = "https://awsimgsrc.dmm.co.jp/pics_dig"

_THUMB_SUFFIX = "pl.jpg"
_POSTER_SUFFIX = "ps.jpg"
# aws 有副本的路径前缀; mono 单独处理, 因为它需要改写路径.
_DIGITAL_HIGH_RES_PREFIXES = ("digital/video/", "digital/amateur/")
_MONO_MOVIE_PREFIX = "mono/movie/"


def _strip_transform(url: str) -> str:
    """去掉图床的缩放查询串; 非 DMM 图床的地址原样返回.

    高清图床按查询串返回缩放后的缩略图, 标准图床忽略查询串, 两处一律去掉.
    """
    if not url.startswith((f"{PICS_CDN}/", f"{HIGH_RES_CDN}/")):
        return url
    return url.split("?", 1)[0].split("#", 1)[0]


def _canonical_path(rel_path: str) -> str:
    """省去 mono 路径在 cid 前多出的分类目录."""
    segments = rel_path[len(_MONO_MOVIE_PREFIX) :].split("/")
    return f"{_MONO_MOVIE_PREFIX}{'/'.join(segments[-2:])}" if len(segments) > 2 else rel_path


def high_res_url(pics_url: str) -> str | None:
    """pics 绝对地址 → aws 绝对地址; 非 pics 域名或该分类无 aws 副本时返回 None."""
    if not pics_url.startswith(f"{PICS_CDN}/"):
        return None
    rel_path = pics_url[len(PICS_CDN) + 1 :]
    if rel_path.startswith(_MONO_MOVIE_PREFIX):
        return f"{HIGH_RES_CDN}/{_canonical_path(rel_path)}"
    if rel_path.startswith(_DIGITAL_HIGH_RES_PREFIXES):
        return f"{HIGH_RES_CDN}/{rel_path}"
    return None


def image_candidates(image_url: str) -> list[str]:
    """图片地址 → 高清候选在前、标准地址兜底的候选列表.

    已是高清地址时不重复推导. aws 通常分辨率更高, 但老片与标准地址同尺寸、少量条目反而更小,
    因此标准地址必须保留; aws 缺图返回 404, pics 缺图返回 302 到 now_printing 占位图,
    两者都由下载期按序回退.
    """
    url = _strip_transform(image_url)
    if url.startswith(f"{HIGH_RES_CDN}/"):
        return [url]
    high_res = high_res_url(url)
    return [high_res, url] if high_res else [url]


def poster_candidates(cover_url: str) -> list[str]:
    """封面地址 (pl 形态) → 竖版海报候选; 不是 pl 形态时返回空."""
    url = _strip_transform(cover_url)
    if not url.endswith(_THUMB_SUFFIX):
        return []
    return image_candidates(f"{url.removesuffix(_THUMB_SUFFIX)}{_POSTER_SUFFIX}")
