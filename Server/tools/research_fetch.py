"""
Low-level research I/O: web search, HTML page extraction, text PDF reading.

All extractors return compact, token-budget-friendly text — never raw HTML/PDF bytes.
"""

from __future__ import annotations

import io
import re
from typing import Any, Optional
from urllib.parse import urlparse

import requests

try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None  # type: ignore

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover
    PdfReader = None  # type: ignore

try:
    from tavily import TavilyClient
except ImportError:  # pragma: no cover
    TavilyClient = None  # type: ignore


USER_AGENT = (
    "Mozilla/5.0 (compatible; AVA-Research/2.0; +https://localhost) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 25
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024  # 15 MB hard cap for downloads


def approx_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)


def truncate_text(text: str, max_chars: int, suffix: str = "\n…[truncated]") -> str:
    if max_chars <= 0 or not text:
        return ""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    keep = max(0, max_chars - len(suffix))
    return text[:keep].rstrip() + suffix


def _clean_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_html_text(html: str, max_chars: int = 8000) -> dict[str, Any]:
    """Parse HTML into readable text; drop scripts/nav noise."""
    title = ""
    if not html:
        return {"title": "", "text": "", "truncated": False}

    if BeautifulSoup is None:
        # Crude fallback: strip tags
        raw = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
        raw = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", raw)
        raw = re.sub(r"(?is)<[^>]+>", " ", raw)
        text = _clean_whitespace(re.sub(r"\s+", " ", raw))
        truncated = len(text) > max_chars
        return {"title": title, "text": truncate_text(text, max_chars), "truncated": truncated}

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        tag.decompose()
    for tag in soup.find_all(["nav", "footer", "header", "aside"]):
        # Keep header if it only has the title; otherwise drop chrome
        tag.decompose()

    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    main = (
        soup.find("article")
        or soup.find("main")
        or soup.find(attrs={"role": "main"})
        or soup.body
        or soup
    )
    text = main.get_text(separator="\n", strip=True) if main else ""
    text = _clean_whitespace(text)
    truncated = len(text) > max_chars
    return {
        "title": title,
        "text": truncate_text(text, max_chars),
        "truncated": truncated,
    }


def extract_pdf_text(data: bytes, max_chars: int = 12000, max_pages: int = 40) -> dict[str, Any]:
    """Extract text from a text-based PDF. Image-only PDFs return an empty note."""
    if PdfReader is None:
        return {
            "text": "",
            "pages": 0,
            "truncated": False,
            "error": "pypdf not installed on server",
        }
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:
        return {"text": "", "pages": 0, "truncated": False, "error": f"PDF open failed: {e}"}

    pages = len(reader.pages)
    chunks: list[str] = []
    total = 0
    truncated = False
    for i, page in enumerate(reader.pages):
        if i >= max_pages:
            truncated = True
            break
        try:
            part = page.extract_text() or ""
        except Exception:
            part = ""
        part = _clean_whitespace(part)
        if not part:
            continue
        if total + len(part) > max_chars:
            remain = max_chars - total
            if remain > 100:
                chunks.append(truncate_text(part, remain))
            truncated = True
            break
        chunks.append(part)
        total += len(part)

    text = _clean_whitespace("\n\n".join(chunks))
    if not text:
        return {
            "text": "",
            "pages": pages,
            "truncated": False,
            "error": "No extractable text (likely scanned/image PDF — not supported yet)",
        }
    return {"text": text, "pages": pages, "truncated": truncated, "error": None}


class ResearchFetchTools:
    """Bounded search / fetch / PDF tools for the research agent."""

    def __init__(self, tavily_key: Optional[str] = None):
        t_key = tavily_key or os.getenv("TAVILY_API_KEY")
        self.tavily = None
        if t_key and TavilyClient is not None:
            try:
                self.tavily = TavilyClient(api_key=t_key)
            except Exception:
                self.tavily = None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # ── web search ──────────────────────────────────────────────────────────

    def web_search(
        self,
        query: str,
        *,
        max_results: int = 5,
        search_depth: str = "basic",
    ) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"status": "error", "error": "empty query", "results": []}

        if self.tavily is not None:
            try:
                depth = "advanced" if search_depth == "advanced" else "basic"
                raw = self.tavily.search(
                    query=query,
                    search_depth=depth,
                    max_results=max(1, min(int(max_results), 10)),
                    include_answer=True,
                    include_raw_content=False,
                )
                results = []
                for r in raw.get("results") or []:
                    results.append(
                        {
                            "title": (r.get("title") or "").strip(),
                            "url": (r.get("url") or "").strip(),
                            "snippet": truncate_text(r.get("content") or "", 500),
                            "score": r.get("score"),
                        }
                    )
                return {
                    "status": "success",
                    "query": query,
                    "answer": truncate_text(raw.get("answer") or "", 800),
                    "results": results,
                    "provider": "tavily",
                }
            except Exception as e:
                # Fall through to google fallback
                tavily_err = str(e)
        else:
            tavily_err = "tavily unavailable"

        # Fallback 1: DuckDuckGo HTML (googlesearch-python often returns empty lists)
        ddg_err = "not attempted"
        try:
            from urllib.parse import parse_qs, unquote

            headers = {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
            resp = self.session.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                headers=headers,
                timeout=DEFAULT_TIMEOUT,
            )
            resp.raise_for_status()
            if BeautifulSoup is None:
                raise RuntimeError("BeautifulSoup unavailable")
            soup = BeautifulSoup(resp.text, "html.parser")
            results = []
            seen: set[str] = set()
            limit = max(1, min(int(max_results), 8))

            def _unwrap(href: str) -> str:
                if not href:
                    return ""
                raw = href.strip()
                if raw.startswith("//"):
                    raw = "https:" + raw
                try:
                    parsed = urlparse(raw)
                    qs = parse_qs(parsed.query)
                    if qs.get("uddg"):
                        return unquote(qs["uddg"][0])
                    if qs.get("u"):
                        return unquote(qs["u"][0])
                except Exception:
                    pass
                return raw

            for block in soup.select("div.result"):
                a = block.select_one("a.result__a")
                if not a:
                    continue
                url = _unwrap(a.get("href") or "")
                if not url.startswith(("http://", "https://")):
                    continue
                host = (urlparse(url).netloc or "").lower()
                if not host or "duckduckgo.com" in host:
                    continue
                if url in seen:
                    continue
                seen.add(url)
                snip_el = block.select_one("a.result__snippet") or block.select_one(
                    ".result__snippet"
                )
                snippet = snip_el.get_text(" ", strip=True) if snip_el else ""
                title = a.get_text(" ", strip=True) or host
                results.append(
                    {
                        "title": title,
                        "url": url,
                        "snippet": truncate_text(snippet, 500),
                    }
                )
                if len(results) >= limit:
                    break

            if results:
                return {
                    "status": "success",
                    "query": query,
                    "answer": "",
                    "results": results,
                    "provider": "duckduckgo",
                    "note": f"tavily fallback used ({tavily_err})",
                }
            ddg_err = "empty results"
        except Exception as e:
            ddg_err = str(e)

        # Fallback 2: googlesearch-python (URLs only; often blocked)
        try:
            from googlesearch import search as gsearch

            urls = list(gsearch(query, num_results=max(1, min(int(max_results), 8))))
            results = [
                {"title": urlparse(u).netloc, "url": u, "snippet": ""} for u in urls if u
            ]
            if results:
                return {
                    "status": "success",
                    "query": query,
                    "answer": "",
                    "results": results,
                    "provider": "googlesearch",
                    "note": f"tavily/ddg fallback used (tavily={tavily_err}; ddg={ddg_err})",
                }
            return {
                "status": "error",
                "error": (
                    f"search failed: tavily={tavily_err}; ddg={ddg_err}; "
                    "googlesearch returned no results"
                ),
                "query": query,
                "results": [],
            }
        except Exception as e:
            return {
                "status": "error",
                "error": (
                    f"search failed: tavily={tavily_err}; ddg={ddg_err}; "
                    f"googlesearch={e}"
                ),
                "query": query,
                "results": [],
            }

    # ── page fetch ──────────────────────────────────────────────────────────

    def fetch_page(self, url: str, *, max_chars: int = 8000) -> dict[str, Any]:
        url = (url or "").strip()
        if not url:
            return {"status": "error", "error": "empty url"}
        if not url.startswith(("http://", "https://")):
            return {"status": "error", "error": "url must be http(s)"}

        # PDFs should go through read_pdf
        if url.lower().split("?")[0].endswith(".pdf"):
            return {
                "status": "error",
                "error": "URL looks like a PDF — use read_pdf instead",
                "url": url,
            }

        try:
            resp = self.session.get(
                url,
                timeout=DEFAULT_TIMEOUT,
                stream=True,
                allow_redirects=True,
            )
            resp.raise_for_status()
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if "pdf" in ctype:
                return {
                    "status": "error",
                    "error": "Content-Type is PDF — use read_pdf instead",
                    "url": str(resp.url),
                }

            # Stream with size cap
            chunks: list[bytes] = []
            size = 0
            for block in resp.iter_content(chunk_size=64 * 1024):
                if not block:
                    continue
                size += len(block)
                if size > MAX_DOWNLOAD_BYTES:
                    return {
                        "status": "error",
                        "error": f"page exceeds {MAX_DOWNLOAD_BYTES} bytes",
                        "url": str(resp.url),
                    }
                chunks.append(block)
            raw = b"".join(chunks)
            # Prefer declared encoding, fall back
            encoding = resp.encoding or "utf-8"
            try:
                html = raw.decode(encoding, errors="replace")
            except Exception:
                html = raw.decode("utf-8", errors="replace")

            parsed = extract_html_text(html, max_chars=max_chars)
            return {
                "status": "success",
                "url": str(resp.url),
                "title": parsed["title"],
                "text": parsed["text"],
                "truncated": parsed["truncated"],
                "approx_tokens": approx_tokens(parsed["text"]),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "url": url}

    # ── PDF ─────────────────────────────────────────────────────────────────

    def read_pdf(
        self,
        url: str,
        *,
        max_chars: int = 12000,
        max_pages: int = 40,
    ) -> dict[str, Any]:
        url = (url or "").strip()
        if not url:
            return {"status": "error", "error": "empty url"}
        if not url.startswith(("http://", "https://")):
            return {"status": "error", "error": "url must be http(s)"}

        try:
            resp = self.session.get(
                url,
                timeout=DEFAULT_TIMEOUT,
                stream=True,
                allow_redirects=True,
            )
            resp.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for block in resp.iter_content(chunk_size=64 * 1024):
                if not block:
                    continue
                size += len(block)
                if size > MAX_DOWNLOAD_BYTES:
                    return {
                        "status": "error",
                        "error": f"PDF exceeds {MAX_DOWNLOAD_BYTES} bytes",
                        "url": str(resp.url),
                    }
                chunks.append(block)
            data = b"".join(chunks)
            if not data.startswith(b"%PDF") and "pdf" not in (
                resp.headers.get("Content-Type") or ""
            ).lower():
                # Still try — some servers mislabel content-type
                pass

            extracted = extract_pdf_text(data, max_chars=max_chars, max_pages=max_pages)
            if extracted.get("error") and not extracted.get("text"):
                return {
                    "status": "error",
                    "error": extracted["error"],
                    "url": str(resp.url),
                    "pages": extracted.get("pages", 0),
                }
            return {
                "status": "success",
                "url": str(resp.url),
                "pages": extracted.get("pages", 0),
                "text": extracted.get("text") or "",
                "truncated": bool(extracted.get("truncated")),
                "approx_tokens": approx_tokens(extracted.get("text") or ""),
                "note": extracted.get("error"),
            }
        except Exception as e:
            return {"status": "error", "error": str(e), "url": url}

    # ── Instagram (public profile text) ─────────────────────────────────────

    @staticmethod
    def _normalize_instagram_username(username: str) -> str:
        u = (username or "").strip()
        u = u.lstrip("@")
        # Accept full URLs
        m = re.search(
            r"(?:instagram\.com|instagr\.am)/([A-Za-z0-9._]+)",
            u,
            flags=re.I,
        )
        if m:
            u = m.group(1)
        u = u.split("?")[0].split("/")[0].strip()
        return u

    def instagram_profile(
        self,
        username: str,
        *,
        max_posts: int = 6,
    ) -> dict[str, Any]:
        """
        Public Instagram profile snapshot for person research (levels 4–5).

        Tries instaloader first (best metadata), then a lightweight web
        profile endpoint / HTML fallback. Returns text-only fields — no media.
        """
        uname = self._normalize_instagram_username(username)
        if not uname or not re.fullmatch(r"[A-Za-z0-9._]{1,30}", uname):
            return {
                "status": "error",
                "error": "invalid Instagram username",
                "username": username,
            }

        max_posts = max(0, min(int(max_posts or 0), 12))
        profile_url = f"https://www.instagram.com/{uname}/"

        # 1) instaloader
        insta_err = "instaloader not attempted"
        try:
            import instaloader  # type: ignore

            loader = instaloader.Instaloader(
                download_pictures=False,
                download_videos=False,
                download_video_thumbnails=False,
                download_geotags=False,
                download_comments=False,
                save_metadata=False,
                compress_json=False,
                quiet=True,
            )
            profile = instaloader.Profile.from_username(loader.context, uname)
            posts_out: list[dict[str, Any]] = []
            if max_posts > 0 and not profile.is_private:
                try:
                    for i, post in enumerate(profile.get_posts()):
                        if i >= max_posts:
                            break
                        cap = (post.caption or "").strip()
                        posts_out.append(
                            {
                                "date": str(getattr(post, "date_utc", "") or ""),
                                "likes": getattr(post, "likes", None),
                                "comments": getattr(post, "comments", None),
                                "caption": truncate_text(cap, 400),
                                "url": f"https://www.instagram.com/p/{post.shortcode}/",
                            }
                        )
                except Exception as post_err:
                    posts_out = [{"note": f"posts unavailable: {post_err}"}]

            return {
                "status": "success",
                "provider": "instaloader",
                "username": profile.username,
                "full_name": profile.full_name,
                "biography": truncate_text(profile.biography or "", 800),
                "followers": profile.followers,
                "following": profile.followees,
                "posts_count": profile.mediacount,
                "is_private": bool(profile.is_private),
                "is_verified": bool(profile.is_verified),
                "external_url": profile.external_url or "",
                "profile_url": profile_url,
                "recent_posts": posts_out,
                "approx_tokens": approx_tokens(
                    f"{profile.full_name} {profile.biography} "
                    + " ".join(
                        p.get("caption", "")
                        for p in posts_out
                        if isinstance(p, dict)
                    )
                ),
            }
        except ImportError:
            insta_err = "instaloader not installed"
        except Exception as e:
            insta_err = str(e)

        # 2) Public web_profile_info style endpoint (often rate-limited)
        web_err = "not attempted"
        try:
            api_url = (
                "https://www.instagram.com/api/v1/users/web_profile_info/"
                f"?username={uname}"
            )
            headers = {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "X-IG-App-ID": "936619743392459",  # public web app id
                "Referer": profile_url,
            }
            resp = self.session.get(api_url, headers=headers, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                user = (data.get("data") or {}).get("user") or {}
                if user:
                    posts_out = []
                    edges = (
                        ((user.get("edge_owner_to_timeline_media") or {}).get("edges"))
                        or []
                    )
                    for edge in edges[:max_posts]:
                        node = edge.get("node") or {}
                        cap_edges = (
                            (node.get("edge_media_to_caption") or {}).get("edges") or []
                        )
                        cap = ""
                        if cap_edges:
                            cap = (cap_edges[0].get("node") or {}).get("text") or ""
                        shortcode = node.get("shortcode") or ""
                        posts_out.append(
                            {
                                "date": str(node.get("taken_at_timestamp") or ""),
                                "likes": (node.get("edge_liked_by") or {}).get("count"),
                                "comments": (
                                    node.get("edge_media_to_comment") or {}
                                ).get("count"),
                                "caption": truncate_text(cap, 400),
                                "url": (
                                    f"https://www.instagram.com/p/{shortcode}/"
                                    if shortcode
                                    else ""
                                ),
                            }
                        )
                    bio = user.get("biography") or ""
                    return {
                        "status": "success",
                        "provider": "web_profile_info",
                        "username": user.get("username") or uname,
                        "full_name": user.get("full_name") or "",
                        "biography": truncate_text(bio, 800),
                        "followers": (user.get("edge_followed_by") or {}).get("count"),
                        "following": (user.get("edge_follow") or {}).get("count"),
                        "posts_count": (
                            user.get("edge_owner_to_timeline_media") or {}
                        ).get("count"),
                        "is_private": bool(user.get("is_private")),
                        "is_verified": bool(user.get("is_verified")),
                        "external_url": user.get("external_url") or "",
                        "profile_url": profile_url,
                        "recent_posts": posts_out,
                        "approx_tokens": approx_tokens(bio + " " + str(posts_out)),
                    }
            web_err = f"http {resp.status_code}"
        except Exception as e:
            web_err = str(e)

        # 3) Last resort: search snippets mentioning the profile
        search = self.web_search(
            f"site:instagram.com/{uname} OR instagram.com/{uname}",
            max_results=5,
            search_depth="basic",
        )
        return {
            "status": "partial",
            "provider": "search_fallback",
            "username": uname,
            "profile_url": profile_url,
            "error": (
                f"Direct Instagram access failed "
                f"(instaloader={insta_err or 'n/a'}; web={web_err}). "
                "Returning related search hits — try another username spelling."
            ),
            "search": search,
            "approx_tokens": approx_tokens(json_dumps_safe(search)),
        }


def json_dumps_safe(obj: Any) -> str:
    try:
        import json

        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return str(obj)
