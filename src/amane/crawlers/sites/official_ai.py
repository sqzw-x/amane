"""AI 官网发现: 没有标准来源时, 让模型推断厂商官网再抓取.

只用在别处都拿不到数据的欧美条目. 链路:

    1. 模型按标题与厂牌名给出候选域名 (训练知识, 常常只是近似名)
    2. 对每个候选试几种常见变体 (www. / official 前缀 / 裸域), 抓首页验证
    3. 命中的首页交给 ``generic_official`` 的标准位置提取

"找到官网"这一步必须联网验证: 模型不知道 ``official`` 这种前缀, 但变体试探能补上.
"读官网"这一步不调模型: 标准位置提取是确定的, 同一份正文永远得到同一份结果.

默认不启用: 每次推断要消耗大量推理 token, 且准确率取决于厂牌知名度. 由
``site_config.official_ai`` 的 api_token / base_url / model 配置, 用户手动决定何时开启.
"""

from __future__ import annotations

import re
from typing import Any, override

from ...enums import SiteName
from ...net.errors import FailureReason, RequestError
from ...plugins.models import SourceTrait
from ..base import Crawler, CrawlerProfile
from ..models import FetchOptions, MediaMetadata, SearchQuery
from .generic_official import GenericOfficialCrawler

#: 候选域名的常见前缀; 模型给的常常是裸名, 官网实际是 official<name>.com.
_HOST_VARIANTS = ("{domain}", "www.{domain}", "official{domain}", "www.official{domain}")
#: 抓首页验证时, 页面至少要有这么多字节才当作真实站点 (占位页往往很小).
_MIN_PAGE_BYTES = 4000
#: 单次最多验证的候选域名数, 避免一次刮削打出十几个请求.
_MAX_CANDIDATES = 3
#: 模型推理很慢 (实测单次 30 秒以上), 默认的 10 秒抓不到应答.
_LLM_TIMEOUT_S = 90.0
#: 候选域名探测: 不存在的域会一直挂到超时, 缩短才能快点换下一个.
_PROBE_TIMEOUT_S = 6.0

#: 平台站不是厂牌官网: 平台上的创作者页也不是, 模型常把它们混为一谈.
_PLATFORM_DOMAINS = frozenset({"onlyfans.com", "fansly.com", "manyvids.com", "fansdb.cc", "theporndb.net"})
#: 厂牌名里的来源前缀与平台后缀, 推断前先剥掉 (TPDB 写作 "FansDB: Dreddxxx (onlyfans)").
_STUDIO_PREFIX = re.compile(r"^[^:]{2,20}:\s*")
_STUDIO_PLATFORM = re.compile(r"\s*\((?:[^)]*(?:onlyfans|fansly|manyvids|pornhub)[^)]*)\)\s*$", re.IGNORECASE)

_INFER_PROMPT = """A Western adult film has these metadata:
title: {title}
studio: {studio}

Find the studio's own official website. Rules:
- NEVER answer with a platform or database host: onlyfans.com, fansly.com, manyvids.com,
  fansdb.cc, theporndb.net, iafd.com, adultfilmdatabase.com
- The studio name above is the creator/producer brand, not a platform
Answer with a JSON array of domain strings only, most likely first, at most 3: ["a.com","b.com"]"""


class AiOfficialCrawler(Crawler):
    """模型推断官网域名 → 抓取验证 → 标准位置提取."""

    @classmethod
    def profile(cls) -> CrawlerProfile:
        return CrawlerProfile(
            name=SiteName.OFFICIAL_AI,
            base_url="",
            traits=frozenset({SourceTrait.NEEDS_PARTIAL}),
        )

    @override
    async def fetch(self, query: SearchQuery, options: FetchOptions | None = None) -> MediaMetadata | None:
        partial = query.partial_result
        if partial is None:
            return None
        domains = await self._infer_domains(partial.title or "", _clean_studio(partial.studio or ""))
        if not domains:
            return None
        reader = GenericOfficialCrawler(client=self.client)
        for url in self._candidate_urls(domains):
            try:
                if not await self._reachable(url):
                    continue
                meta = await reader._extract(url, query.number)
            except RequestError as exc:
                if exc.reason is FailureReason.NOT_FOUND:
                    continue
                raise
            if meta is not None:
                return meta
        return None

    async def _infer_domains(self, title: str, studio: str) -> list[str]:
        """模型给候选域名; 没配凭据或回复不成形时返回空列表."""
        token = self.config.api_token if self.config is not None else None
        base_url = (self.config.base_url if self.config is not None else None) or "https://api.deepseek.com/v1"
        model = (self.config.model if self.config is not None else None) or "deepseek-flash"
        if not token:
            return []
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": _INFER_PROMPT.format(title=title, studio=studio)}],
            # 这类模型的思考开销很大, 预算给小了回复会是空的.
            "max_tokens": 8000,
        }
        try:
            reply = await self.client.post_json(
                f"{base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_LLM_TIMEOUT_S,
            )
        except RequestError:
            return []
        return _parse_domains(reply)

    def _candidate_urls(self, domains: list[str]) -> list[str]:
        usable = [domain for domain in domains if _registrable(domain) not in _PLATFORM_DOMAINS]
        return [
            f"https://{pattern.format(domain=domain)}/"
            for domain in usable[:_MAX_CANDIDATES]
            for pattern in _HOST_VARIANTS
        ]

    async def _reachable(self, url: str) -> bool:
        try:
            # 用 get_text 而非 get_html: 后者会把带 age-gate 脚本的正常页面判成年龄门.
            text = await self.client.get_text(url, timeout=_PROBE_TIMEOUT_S)
        except RequestError as exc:
            # 域不存在 / 连不上 / 超时都算"这条路走不通", 换下一个候选.
            if exc.reason in (FailureReason.NOT_FOUND, FailureReason.NETWORK, FailureReason.TIMEOUT):
                return False
            raise
        return len(text) >= _MIN_PAGE_BYTES

    async def _search(self, query: SearchQuery, options: FetchOptions | None = None) -> str | None:
        return None

    async def _scrape(self, url: str, options: FetchOptions | None = None) -> MediaMetadata | None:
        return None


def _clean_studio(studio: str) -> str:
    """去掉来源前缀与平台后缀: "FansDB: Dreddxxx (onlyfans)" → "Dreddxxx"."""
    value = _STUDIO_PREFIX.sub("", studio.strip())
    return _STUDIO_PLATFORM.sub("", value).strip()


def _registrable(domain: str) -> str:
    """取末两段作为可注册域, 用来比对平台黑名单 (www.onlyfans.com → onlyfans.com)."""
    parts = domain.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else domain.lower()


def _parse_domains(reply: Any) -> list[str]:
    """从聊天应答里取域名列表; 回复常带说明文字, 只认第一个 JSON 数组."""
    if not isinstance(reply, dict):
        return []
    choices = reply.get("choices")
    if not isinstance(choices, list) or not choices:
        return []
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        return []
    found = re.search(r"\[[^\]]*\]", content, re.DOTALL)
    if found is None:
        return []
    domains: list[str] = []
    for item in re.findall(r'"([a-z0-9][a-z0-9.\-]*\.[a-z]{2,})"', found.group(0), re.IGNORECASE):
        domain = item.strip().lower()
        if domain not in domains:
            domains.append(domain)
    return domains


__all__ = ["AiOfficialCrawler"]
