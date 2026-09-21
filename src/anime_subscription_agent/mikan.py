"""Strictly read-only public Mikan Project search and RSS connector."""

from __future__ import annotations

import html
import re
import unicodedata
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class MikanError(RuntimeError):
    """Base error for public Mikan reads."""


class MikanPolicyError(MikanError):
    """Raised before a request outside the public read allowlist is sent."""


class MikanRequestError(MikanError):
    """The public Mikan service could not be read."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class MikanProtocolError(MikanError):
    """A Mikan page or feed did not match the expected public structure."""


class ResponseLike(Protocol):
    def read(self, amount: int | None = None) -> bytes: ...

    def __enter__(self) -> "ResponseLike": ...

    def __exit__(self, *args: object) -> None: ...


class OpenerLike(Protocol):
    def open(self, request: Request, timeout: float) -> ResponseLike: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_ALLOWED_HOSTS = frozenset({"mikanani.me", "mikanime.tv"})
_BANGUMI_PATH = re.compile(r"/Home/Bangumi/[1-9][0-9]*\Z")
_RSS_PATH = "/RSS/Bangumi"
_MAX_TITLE_LENGTH = 200
_MAX_BANGUMI_RESULTS = 8
_MAX_GROUPS_PER_BANGUMI = 20
_DEFAULT_CANDIDATE_LIMIT = 5
_MAX_CANDIDATE_LIMIT = 10
_MAX_REDIRECTS = 2
_MAX_INSPECTION_SAMPLES = 5


def _normalise(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value).casefold().split())


def canonical_public_url(value: str, base_url: str) -> str:
    """Return an allowlisted public Mikan URL on the configured mirror.

    The path and query are preserved while the host is intentionally replaced,
    so equivalent `.me` and `.tv` links cannot become separate subscriptions.
    """
    parsed = urlsplit(value)
    base = urlsplit(base_url.rstrip("/"))
    if (
        base.scheme != "https"
        or base.hostname not in _ALLOWED_HOSTS
        or base.username
        or base.password
        or base.port not in {None, 443}
        or base.path not in {"", "/"}
        or base.query
        or base.fragment
    ):
        raise MikanPolicyError("Configured Mikan mirror is outside the public allowlist.")
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in {None, 443}
        or parsed.fragment
    ):
        raise MikanPolicyError("Mikan URL is outside the public mirror allowlist.")
    query = parse_qs(parsed.query, keep_blank_values=True)
    if _BANGUMI_PATH.fullmatch(parsed.path) and not query:
        return f"{base.scheme}://{base.netloc}{parsed.path}"
    if (
        parsed.path == _RSS_PATH
        and set(query) == {"bangumiId", "subgroupid"}
        and all(len(query[key]) == 1 and query[key][0].isdigit() and int(query[key][0]) > 0 for key in query)
    ):
        return f"{base.scheme}://{base.netloc}{parsed.path}?{urlencode({key: query[key][0] for key in sorted(query)})}"
    raise MikanPolicyError("Mikan URL is outside the public read-only allowlist.")


class _AnchorCollector(HTMLParser):
    """Collect anchors in document order without executing page JavaScript."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = " ".join("".join(self._parts).split())
            self.anchors.append((self._href, html.unescape(text)))
            self._href = None
            self._parts = []


def _anchors(document: str) -> list[tuple[str, str]]:
    parser = _AnchorCollector()
    parser.feed(document)
    parser.close()
    return parser.anchors


def _page_title(document: str) -> str:
    match = re.search(
        r'<p[^>]+class=["\'][^"\']*bangumi-title[^"\']*["\'][^>]*>(.*?)</p>',
        document,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        raise MikanProtocolError("Mikan detail page did not contain a bangumi title.")
    title_html = re.sub(r"<a\b[^>]*>.*?</a>", "", match.group(1), flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "", title_html)
    text = " ".join(html.unescape(text).split())
    if not text:
        raise MikanProtocolError("Mikan detail page contained an empty bangumi title.")
    return text


def _languages(titles: list[str]) -> list[str]:
    joined = " ".join(titles).upper()
    languages: list[str] = []
    if any(marker in joined for marker in ("简繁", "简中", "简体", "CHS")):
        languages.append("简体中文")
    if any(marker in joined for marker in ("简繁", "繁中", "繁体", "CHT")):
        languages.append("繁体中文")
    if any(marker in joined for marker in ("日语", "JPN")):
        languages.append("日语")
    if any(marker in joined for marker in ("英语", "ENG")):
        languages.append("英语")
    return languages


def _resolutions(titles: list[str]) -> list[str]:
    values: set[str] = set()
    for title in titles:
        values.update(match.lower() for match in re.findall(r"\b(?:2160|1440|1080|720|480)p\b", title, re.I))
    return sorted(values, key=lambda value: int(value[:-1]), reverse=True)


@dataclass(frozen=True, slots=True)
class MikanConfig:
    base_url: str = "https://mikanime.tv"
    timeout_seconds: float = 15.0
    max_response_bytes: int = 5 * 1024 * 1024

    def __post_init__(self) -> None:
        parsed = urlsplit(self.base_url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _ALLOWED_HOSTS
            or parsed.port not in {None, 443}
        ):
            raise ValueError("base_url must use HTTPS on an allowed public Mikan host")
        if parsed.username or parsed.password or parsed.path not in {"", "/"}:
            raise ValueError("base_url must contain only an allowed host")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        if self.timeout_seconds <= 0 or self.max_response_bytes <= 0:
            raise ValueError("timeout and response size limits must be positive")


class MikanReadOnlyConnector:
    """Public Mikan reader restricted to search, detail, and group RSS GETs."""

    def __init__(
        self,
        base_url: str = "https://mikanime.tv",
        *,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = 5 * 1024 * 1024,
        opener: OpenerLike | None = None,
    ) -> None:
        self.config = MikanConfig(
            base_url=base_url.rstrip("/"),
            timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )
        # Public Mikan access may require the user's configured outbound proxy.
        # Destination URLs remain constrained by _assert_allowed before opening.
        self._opener = opener or build_opener(_NoRedirect())

    def search_rss(
        self, anime_title: str, fansub_group: str | None = None
    ) -> list[dict[str, Any]]:
        title = self._validate_text(anime_title, "anime_title")
        group_filter = self._validate_text(fansub_group, "fansub_group", optional=True)
        candidates: list[dict[str, Any]] = []
        for anime in self.search_anime(title)["candidates"]:
            result = self.list_rss_candidates(
                anime["bangumi_id"], fansub_group=group_filter or None,
                limit=_MAX_CANDIDATE_LIMIT,
            )
            candidates.extend(result["candidates"])
        return candidates

    def search_anime(self, anime_title: str) -> dict[str, Any]:
        """Search public titles and return bounded, distinct work/season candidates."""
        title = self._validate_text(anime_title, "anime_title")
        document = self._get_text("/Home/Search", {"searchstr": title})
        matches = self._parse_search_results(document)
        total_count = len(matches)
        selected = matches[:_MAX_BANGUMI_RESULTS]
        candidates: list[dict[str, Any]] = []
        for bangumi_id, search_title in selected:
            detail_path = f"/Home/Bangumi/{bangumi_id}"
            detail_title, _ = self._parse_detail(self._get_text(detail_path), bangumi_id)
            candidates.append(
                {
                    "bangumi_id": bangumi_id,
                    "anime_title": detail_title,
                    "search_title": search_title,
                    "source_page": self._url(detail_path),
                }
            )
        return {
            "query": title,
            "candidate_total": total_count,
            "returned_count": len(candidates),
            "truncated": total_count > len(candidates),
            "candidates": candidates,
        }

    def list_rss_candidates(
        self,
        bangumi_id: int,
        *,
        fansub_group: str | None = None,
        subtitle_language: str | None = None,
        resolution: str | None = None,
        limit: int = _DEFAULT_CANDIDATE_LIMIT,
    ) -> dict[str, Any]:
        """Read bounded subtitle-group candidates for one already selected work."""
        if isinstance(bangumi_id, bool) or not isinstance(bangumi_id, int) or bangumi_id < 1:
            raise ValueError("bangumi_id must be a positive integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_CANDIDATE_LIMIT:
            raise ValueError(f"limit must be an integer from 1 to {_MAX_CANDIDATE_LIMIT}")
        group_filter = self._validate_text(fansub_group, "fansub_group", optional=True)
        language_filter = self._validate_text(subtitle_language, "subtitle_language", optional=True)
        resolution_filter = self._validate_text(resolution, "resolution", optional=True).lower()
        if resolution_filter and resolution_filter not in {"2160p", "1440p", "1080p", "720p", "480p"}:
            raise ValueError("resolution must be one of 2160p, 1440p, 1080p, 720p, or 480p")
        detail_path = f"/Home/Bangumi/{bangumi_id}"
        detail_title, groups = self._parse_detail(self._get_text(detail_path), bangumi_id)
        if len(groups) > _MAX_GROUPS_PER_BANGUMI:
            raise MikanProtocolError("Mikan anime has too many groups; specify fansub_group.")
        matches: list[dict[str, Any]] = []
        for group in groups:
            group_name = group["fansub_group"]
            if group_filter and not self._matches_text(group_name, group_filter):
                continue
            feed_document = self._get_text(
                _RSS_PATH,
                {"bangumiId": str(bangumi_id), "subgroupid": str(group["subgroup_id"])},
            )
            titles = self._parse_rss_titles(feed_document)
            languages = _languages(titles)
            resolutions = _resolutions(titles)
            if language_filter and not self._matches_text(" ".join(languages), language_filter):
                continue
            if resolution_filter and resolution_filter not in resolutions:
                continue
            matches.append(
                {
                    "anime_title": detail_title,
                    "bangumi_id": bangumi_id,
                    "fansub_group": group_name,
                    "subgroup_id": group["subgroup_id"],
                    "resolutions": resolutions,
                    "subtitle_languages": languages,
                    "rss_url": self._url(_RSS_PATH, {"bangumiId": str(bangumi_id), "subgroupid": str(group["subgroup_id"])}),
                    "source_page": self._url(detail_path),
                    "rss_item_count": len(titles),
                }
            )
        return {
            "bangumi_id": bangumi_id,
            "anime_title": detail_title,
            "candidate_total": len(matches),
            "returned_count": min(len(matches), limit),
            "truncated": len(matches) > limit,
            "candidates": matches[:limit],
        }

    def inspect_rss_candidate(self, rss_url: str) -> dict[str, Any]:
        """Read one allowlisted public RSS feed and return bounded diagnostics."""
        normalized_url = canonical_public_url(rss_url, self.config.base_url)
        parsed_url = urlsplit(normalized_url)
        query = parse_qs(parsed_url.query, keep_blank_values=True)
        bangumi_id = int(query["bangumiId"][0])
        subgroup_id = int(query["subgroupid"][0])
        document, http_status = self._get_text_response(
            _RSS_PATH,
            {"bangumiId": str(bangumi_id), "subgroupid": str(subgroup_id)},
        )
        base_result: dict[str, Any] = {
            "rss_url": rss_url,
            "normalized_rss_url": normalized_url,
            "bangumi_id": bangumi_id,
            "subgroup_id": subgroup_id,
            "reachable": True,
            "http_status": http_status,
            "feed_title": None,
            "item_count": 0,
            "latest_published_at": None,
            "sample_order": "feed_order_first_5",
            "sample_items": [],
            "diagnostics": [],
        }
        try:
            root = ElementTree.fromstring(document)
        except ElementTree.ParseError:
            return {
                **base_result,
                "status": "invalid_rss",
                "diagnostics": [
                    {
                        "code": "invalid_xml",
                        "message": "The public Mikan response was not valid XML.",
                    }
                ],
            }
        channel = root.find("./channel") if root.tag == "rss" else None
        if channel is None:
            return {
                **base_result,
                "status": "invalid_rss",
                "diagnostics": [
                    {
                        "code": "invalid_rss_structure",
                        "message": "The XML response did not contain an RSS channel.",
                    }
                ],
            }

        diagnostics: list[dict[str, str]] = []
        feed_title = self._element_text(channel.find("title"))
        if feed_title is None:
            diagnostics.append(
                {"code": "feed_title_missing", "message": "The RSS feed has no title."}
            )
        items = channel.findall("item")
        parsed_dates: list[datetime] = []
        sample_items: list[dict[str, Any]] = []
        missing_titles = 0
        missing_dates = 0
        invalid_dates = 0
        for index, item in enumerate(items):
            title = self._element_text(item.find("title"))
            raw_published_at = self._descendant_text(item, "pubDate")
            published_at = self._parse_published_at(raw_published_at)
            if title is None:
                missing_titles += 1
            if raw_published_at is None:
                missing_dates += 1
            elif published_at is None:
                invalid_dates += 1
            else:
                parsed_dates.append(published_at)
            if index < _MAX_INSPECTION_SAMPLES:
                sample_items.append(
                    {
                        "title": title,
                        "published_at": (
                            published_at.isoformat() if published_at is not None else None
                        ),
                        "resolutions": _resolutions([title]) if title else [],
                        "subtitle_languages": _languages([title]) if title else [],
                    }
                )
        if not items:
            diagnostics.append(
                {"code": "empty_feed", "message": "The RSS feed contains no items."}
            )
        if missing_titles:
            diagnostics.append(
                {
                    "code": "item_titles_missing",
                    "message": f"{missing_titles} RSS item(s) have no title.",
                }
            )
        if missing_dates:
            diagnostics.append(
                {
                    "code": "item_dates_missing",
                    "message": f"{missing_dates} RSS item(s) have no publication time.",
                }
            )
        if invalid_dates:
            diagnostics.append(
                {
                    "code": "item_dates_invalid",
                    "message": f"{invalid_dates} RSS item(s) have an unrecognized publication time.",
                }
            )
        latest = max(parsed_dates).isoformat() if parsed_dates else None
        return {
            **base_result,
            "status": "empty_feed" if not items else "ok",
            "feed_title": feed_title,
            "item_count": len(items),
            "latest_published_at": latest,
            "sample_items": sample_items,
            "diagnostics": diagnostics,
        }

    @staticmethod
    def _element_text(element: ElementTree.Element | None) -> str | None:
        if element is None or element.text is None:
            return None
        value = " ".join(element.text.split())
        return value or None

    @classmethod
    def _descendant_text(
        cls, element: ElementTree.Element, local_name: str
    ) -> str | None:
        for child in element.iter():
            if isinstance(child.tag, str) and child.tag.rsplit("}", 1)[-1] == local_name:
                value = cls._element_text(child)
                if value is not None:
                    return value
        return None

    @staticmethod
    def _parse_published_at(value: str | None) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except (TypeError, ValueError, OverflowError):
                return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _matches_text(value: str, filter_value: str) -> bool:
        return _normalise(filter_value) in _normalise(value)

    @staticmethod
    def _validate_text(value: str | None, label: str, optional: bool = False) -> str:
        if value is None and optional:
            return ""
        if not isinstance(value, str):
            raise ValueError(f"{label} must be text")
        result = " ".join(value.split())
        if not result and optional:
            return ""
        if not result:
            raise ValueError(f"{label} is required")
        if len(result) > _MAX_TITLE_LENGTH:
            raise ValueError(f"{label} is too long")
        return result

    def _parse_search_results(self, document: str) -> list[tuple[int, str]]:
        seen: set[int] = set()
        results: list[tuple[int, str]] = []
        for href, text in _anchors(document):
            match = re.fullmatch(r"/Home/Bangumi/([1-9][0-9]*)", href)
            if match and text and int(match.group(1)) not in seen:
                bangumi_id = int(match.group(1))
                seen.add(bangumi_id)
                results.append((bangumi_id, text))
        if not results and "搜索结果" not in document:
            raise MikanProtocolError("Mikan search page structure changed.")
        return results

    def _parse_detail(
        self, document: str, bangumi_id: int
    ) -> tuple[str, list[dict[str, Any]]]:
        title = _page_title(document)
        groups: list[dict[str, Any]] = []
        last_group: tuple[int, str] | None = None
        seen: set[int] = set()
        for href, text in _anchors(document):
            publish_group = re.fullmatch(r"/Home/PublishGroup/([1-9][0-9]*)", href)
            if publish_group and text:
                last_group = (int(publish_group.group(1)), text)
                continue
            parsed = urlsplit(href)
            if parsed.path != _RSS_PATH:
                continue
            query = parse_qs(parsed.query)
            try:
                rss_bangumi_id = int(query["bangumiId"][0])
                subgroup_id = int(query["subgroupid"][0])
            except (KeyError, ValueError, IndexError):
                continue
            if rss_bangumi_id != bangumi_id or not last_group or subgroup_id in seen:
                continue
            seen.add(subgroup_id)
            groups.append(
                {
                    "fansub_group": last_group[1],
                    "subgroup_id": subgroup_id,
                    "publish_group_id": last_group[0],
                }
            )
        if not groups:
            raise MikanProtocolError("Mikan detail page did not contain public group RSS links.")
        return title, groups

    @staticmethod
    def _parse_rss_titles(document: str) -> list[str]:
        try:
            root = ElementTree.fromstring(document)
        except ElementTree.ParseError as exc:
            raise MikanProtocolError("Mikan RSS response was not valid XML.") from exc
        return [
            title.text.strip()
            for title in root.findall("./channel/item/title")
            if title.text and title.text.strip()
        ]

    def _assert_allowed(self, method: str, path: str, params: Mapping[str, str]) -> None:
        if method != "GET":
            raise MikanPolicyError(f"request blocked by read-only policy: {method} {path}")
        if path == "/Home/Search" and set(params) == {"searchstr"}:
            return
        if _BANGUMI_PATH.fullmatch(path) and not params:
            return
        if (
            path == _RSS_PATH
            and set(params) == {"bangumiId", "subgroupid"}
            and all(str(params[key]).isdigit() and int(params[key]) > 0 for key in params)
        ):
            return
        raise MikanPolicyError(f"request blocked by read-only policy: {method} {path}")

    def _url(self, path: str, params: Mapping[str, str] | None = None) -> str:
        params = params or {}
        self._assert_allowed("GET", path, params)
        suffix = f"?{urlencode(params)}" if params else ""
        return f"{self.config.base_url}{path}{suffix}"

    @staticmethod
    def _redirect_path_category(path: str) -> str:
        if path == "/Home/Search":
            return "search"
        if _BANGUMI_PATH.fullmatch(path):
            return "bangumi"
        if path == _RSS_PATH:
            return "rss"
        return "unknown"

    @staticmethod
    def _canonical_read_path(path: str) -> str:
        """Accept only case-normalized spellings of the existing public paths."""
        folded = path.casefold()
        if folded == "/home/search":
            return "/Home/Search"
        match = re.fullmatch(r"/home/bangumi/([1-9][0-9]*)", path, re.IGNORECASE)
        if match:
            return f"/Home/Bangumi/{match.group(1)}"
        if folded == "/rss/bangumi":
            return _RSS_PATH
        raise MikanPolicyError("Mikan redirect changed the requested public resource.")

    def _validate_redirect(
        self, current_url: str, location: str | None, expected_path: str,
        expected_params: Mapping[str, str],
    ) -> str:
        """Allow only mirror redirects which retain the exact read request."""
        if not location:
            raise MikanRequestError("Mikan redirect without a location was blocked.")
        target = urlsplit(urljoin(current_url, location))
        try:
            target_port = target.port
        except ValueError as exc:
            raise MikanPolicyError("Mikan redirect used an invalid port.") from exc
        if (
            target.scheme != "https"
            or target.hostname not in _ALLOWED_HOSTS
            or target.username
            or target.password
            or target_port not in {None, 443}
            or target.fragment
        ):
            raise MikanPolicyError("Mikan redirect target is outside the public mirror allowlist.")
        pairs = parse_qsl(target.query, keep_blank_values=True)
        target_params = dict(pairs)
        if len(pairs) != len(target_params):
            raise MikanPolicyError("Mikan redirect repeated a query parameter.")
        expected = {key: str(value) for key, value in expected_params.items()}
        canonical_path = self._canonical_read_path(target.path)
        if canonical_path != expected_path or target_params != expected:
            raise MikanPolicyError("Mikan redirect changed the requested public resource.")
        self._assert_allowed("GET", canonical_path, target_params)
        return target.geturl()

    def _get_text_response(
        self, path: str, params: Mapping[str, str] | None = None
    ) -> tuple[str, int]:
        url = self._url(path, params)
        expected_params = params or {}
        visited = {url}
        redirects = 0
        while True:
            request = Request(
                url,
                headers={"User-Agent": "anime-subscription-agent/0.1 (read-only)"},
                method="GET",
            )
            try:
                with self._opener.open(request, timeout=self.config.timeout_seconds) as response:
                    raw = response.read(self.config.max_response_bytes + 1)
                    status = getattr(response, "status", 200) or 200
                break
            except HTTPError as exc:
                if 300 <= exc.code < 400:
                    category = self._redirect_path_category(path)
                    if redirects >= _MAX_REDIRECTS:
                        raise MikanRequestError(
                            f"Mikan redirect limit reached (HTTP {exc.code}, {category}).",
                            status_code=exc.code,
                        ) from exc
                    try:
                        url = self._validate_redirect(
                            url, exc.headers.get("Location"), path, expected_params
                        )
                    except MikanPolicyError as policy_error:
                        raise MikanRequestError(
                            f"Mikan redirect was blocked (HTTP {exc.code}, {category}).",
                            status_code=exc.code,
                        ) from policy_error
                    if url in visited:
                        raise MikanRequestError(
                            f"Mikan redirect loop was blocked (HTTP {exc.code}, {category}).",
                            status_code=exc.code,
                        ) from exc
                    visited.add(url)
                    redirects += 1
                    continue
                if exc.code == 404:
                    raise MikanRequestError(
                        "Requested public Mikan page was not found.",
                        status_code=exc.code,
                    ) from exc
                raise MikanRequestError(
                    f"Mikan returned HTTP {exc.code}.", status_code=exc.code
                ) from exc
            except (URLError, TimeoutError, OSError) as exc:
                raise MikanRequestError("Could not reach public Mikan Project.") from exc
        if len(raw) > self.config.max_response_bytes:
            raise MikanProtocolError("Mikan response exceeded the size limit.")
        try:
            return raw.decode("utf-8"), int(status)
        except UnicodeDecodeError as exc:
            raise MikanProtocolError("Mikan response was not UTF-8.") from exc

    def _get_text(self, path: str, params: Mapping[str, str] | None = None) -> str:
        return self._get_text_response(path, params)[0]
