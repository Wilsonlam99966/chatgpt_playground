#!/usr/bin/env python3
"""Technical SEO crawler MVP.

Features:
- Respect robots.txt (basic allow/disallow rules)
- Crawl same-domain pages with URL cap
- Collect status, meta tags, title/H1/canonical details
- Detect common technical SEO issues
- Emit JSON report
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from typing import Dict, List, Optional, Set, Tuple
from urllib import error, parse, request, robotparser


DEFAULT_TIMEOUT = 10
DEFAULT_MAX_URLS = 20_000
ASSUMED_AVG_FETCH_SECONDS = 0.6
ASSUMED_AVG_PAGE_KB = 250


@dataclass
class PageRecord:
    url: str
    status_code: Optional[int]
    content_type: Optional[str]
    title: str
    meta_description: str
    canonical: str
    robots_meta: str
    h1_count: int
    fetch_ms: int
    size_bytes: int
    links: List[str]
    error: str = ""


@dataclass
class Issue:
    code: str
    severity: str
    url: str
    message: str


class SEOHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.meta_description = ""
        self.canonical = ""
        self.robots_meta = ""
        self.h1_count = 0
        self.links: List[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attrs_dict = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            name = attrs_dict.get("name", "").lower()
            if name == "description" and not self.meta_description:
                self.meta_description = attrs_dict.get("content", "").strip()
            elif name == "robots" and not self.robots_meta:
                self.robots_meta = attrs_dict.get("content", "").strip().lower()
        elif tag == "link":
            if attrs_dict.get("rel", "").lower() == "canonical":
                self.canonical = attrs_dict.get("href", "").strip()
        elif tag == "h1":
            self.h1_count += 1
        elif tag == "a":
            href = attrs_dict.get("href", "").strip()
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title and data.strip():
            self.title += data.strip()


def normalize_url(base: str, maybe_relative: str) -> str:
    joined = parse.urljoin(base, maybe_relative)
    split = parse.urlsplit(joined)
    normalized_path = split.path or "/"
    normalized = parse.urlunsplit(
        (
            split.scheme.lower(),
            split.netloc.lower(),
            normalized_path,
            split.query,
            "",  # strip fragment
        )
    )
    return normalized


def same_domain(url: str, domain: str) -> bool:
    return parse.urlsplit(url).netloc.lower() == domain.lower()


def fetch(url: str, user_agent: str, timeout: int) -> Tuple[Optional[int], Dict[str, str], bytes, str]:
    req = request.Request(url, headers={"User-Agent": user_agent})
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None)
            headers = {k.lower(): v for k, v in resp.headers.items()}
            content = resp.read()
            return status, headers, content, ""
    except error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, b"", str(exc)
    except Exception as exc:  # noqa: BLE001
        return None, {}, b"", str(exc)


def load_robots(base_url: str, user_agent: str, timeout: int) -> Tuple[robotparser.RobotFileParser, str]:
    robots_url = parse.urljoin(base_url, "/robots.txt")
    rp = robotparser.RobotFileParser()
    rp.set_url(robots_url)
    try:
        req = request.Request(robots_url, headers={"User-Agent": user_agent})
        with request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            rp.parse(content.splitlines())
            return rp, "ok"
    except error.HTTPError as exc:
        if exc.code in (401, 403):
            # Explicitly blocked by robots endpoint or server policy.
            rp.disallow_all = True
            return rp, "forbidden"
        # robots unreachable/non-200: fail-open
        return rp, "unavailable"
    except Exception:
        # robots unreachable due networking/proxy errors: fail-open
        return rp, "unavailable"


def detect_issues(pages: Dict[str, PageRecord]) -> List[Issue]:
    issues: List[Issue] = []

    title_map: Dict[str, List[str]] = defaultdict(list)
    desc_map: Dict[str, List[str]] = defaultdict(list)

    for page in pages.values():
        if page.status_code is None:
            issues.append(Issue("CRL_001", "critical", page.url, f"Fetch failed: {page.error}"))
            continue
        if page.status_code >= 500:
            issues.append(Issue("STS_5XX", "critical", page.url, f"Server error {page.status_code}"))
        elif page.status_code >= 400:
            issues.append(Issue("STS_4XX", "high", page.url, f"Client error {page.status_code}"))
        elif page.status_code >= 300:
            issues.append(Issue("STS_3XX", "medium", page.url, f"Redirect response {page.status_code}"))

        if not page.title:
            issues.append(Issue("ONP_TITLE_MISSING", "medium", page.url, "Missing <title> tag"))
        else:
            title_map[page.title].append(page.url)

        if not page.meta_description:
            issues.append(Issue("ONP_META_DESC_MISSING", "low", page.url, "Missing meta description"))
        else:
            desc_map[page.meta_description].append(page.url)

        if page.h1_count == 0:
            issues.append(Issue("ONP_H1_MISSING", "medium", page.url, "Missing H1"))
        elif page.h1_count > 1:
            issues.append(Issue("ONP_H1_MULTIPLE", "low", page.url, f"Multiple H1 tags ({page.h1_count})"))

        if not page.canonical:
            issues.append(Issue("IDX_CANONICAL_MISSING", "low", page.url, "Missing canonical tag"))

        if "noindex" in page.robots_meta:
            issues.append(Issue("IDX_NOINDEX", "high", page.url, "Page includes noindex in robots meta"))

    for title, urls in title_map.items():
        if title and len(urls) > 1:
            for u in urls:
                issues.append(Issue("ONP_TITLE_DUPLICATE", "medium", u, f"Duplicate title across {len(urls)} pages"))

    for desc, urls in desc_map.items():
        if desc and len(urls) > 1:
            for u in urls:
                issues.append(Issue("ONP_META_DESC_DUPLICATE", "low", u, f"Duplicate meta description across {len(urls)} pages"))

    return issues


def crawl(
    start_url: str,
    max_urls: int,
    user_agent: str,
    timeout: int,
    ignore_robots: bool = False,
) -> Dict[str, object]:
    start_url = normalize_url(start_url, "/") if parse.urlsplit(start_url).path == "" else normalize_url(start_url, "")
    domain = parse.urlsplit(start_url).netloc
    rp, robots_status = load_robots(start_url, user_agent, timeout)

    queue: deque[str] = deque([start_url])
    visited: Set[str] = set()
    pages: Dict[str, PageRecord] = {}
    broken_links: List[Tuple[str, str]] = []

    while queue and len(visited) < max_urls:
        url = queue.popleft()
        if url in visited:
            continue
        visited.add(url)

        robots_blocked = robots_status == "ok" and not rp.can_fetch(user_agent, url)
        if not ignore_robots and robots_blocked:
            pages[url] = PageRecord(
                url=url,
                status_code=None,
                content_type=None,
                title="",
                meta_description="",
                canonical="",
                robots_meta="",
                h1_count=0,
                fetch_ms=0,
                size_bytes=0,
                links=[],
                error="Blocked by robots.txt",
            )
            continue

        started = time.time()
        status_code, headers, content, fetch_error = fetch(url, user_agent, timeout)
        fetch_ms = int((time.time() - started) * 1000)
        content_type = headers.get("content-type", "")

        parser = SEOHTMLParser()
        links: List[str] = []

        if content and "text/html" in content_type:
            try:
                html = content.decode("utf-8", errors="ignore")
                parser.feed(html)
                links = [normalize_url(url, href) for href in parser.links]
            except Exception as exc:  # noqa: BLE001
                fetch_error = f"HTML parse error: {exc}"

        pages[url] = PageRecord(
            url=url,
            status_code=status_code,
            content_type=content_type,
            title=parser.title.strip(),
            meta_description=parser.meta_description.strip(),
            canonical=normalize_url(url, parser.canonical) if parser.canonical else "",
            robots_meta=parser.robots_meta,
            h1_count=parser.h1_count,
            fetch_ms=fetch_ms,
            size_bytes=len(content),
            links=links,
            error=fetch_error,
        )

        for target in links:
            if same_domain(target, domain):
                if target not in visited and target not in queue and len(visited) + len(queue) < max_urls:
                    queue.append(target)
            else:
                # external link ignored for crawling
                pass

    # Internal broken links check
    for source, record in pages.items():
        for target in record.links:
            if same_domain(target, domain):
                target_page = pages.get(target)
                if target_page and target_page.status_code and target_page.status_code >= 400:
                    broken_links.append((source, target))

    issues = detect_issues(pages)
    for source, target in broken_links:
        issues.append(Issue("LNK_BROKEN_INTERNAL", "high", source, f"Links to broken internal URL: {target}"))

    severity_weights = {"critical": 8, "high": 4, "medium": 2, "low": 1}
    deductions = sum(severity_weights.get(i.severity, 1) for i in issues)
    score = max(0, 100 - deductions)

    summary = {
        "pages_crawled": len(pages),
        "issues_found": len(issues),
        "issues_by_severity": dict(Counter(issue.severity for issue in issues)),
        "health_score": score,
    }

    return {
        "project": {
            "start_url": start_url,
            "domain": domain,
            "max_urls": max_urls,
            "robots_status": robots_status,
            "ignore_robots": ignore_robots,
        },
        "summary": summary,
        "pages": [asdict(p) for p in pages.values()],
        "issues": [asdict(i) for i in issues],
    }


def estimate_feasibility(max_urls: int) -> Dict[str, object]:
    """Provide a rough feasibility estimate for crawl volume planning."""
    est_seconds = max_urls * ASSUMED_AVG_FETCH_SECONDS
    est_hours = round(est_seconds / 3600, 2)
    est_transfer_gb = round((max_urls * ASSUMED_AVG_PAGE_KB) / (1024 * 1024), 2)
    is_feasible_single_run = est_hours <= 24
    recommendation = (
        "Feasible for a single run on most developer machines."
        if is_feasible_single_run
        else "Likely long-running; consider splitting by sitemap/directory or lowering --max-urls."
    )
    return {
        "max_urls": max_urls,
        "assumptions": {
            "avg_fetch_seconds_per_url": ASSUMED_AVG_FETCH_SECONDS,
            "avg_page_kb": ASSUMED_AVG_PAGE_KB,
        },
        "estimate": {
            "runtime_hours_single_worker": est_hours,
            "network_transfer_gb": est_transfer_gb,
        },
        "feasible_single_run": is_feasible_single_run,
        "recommendation": recommendation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a technical SEO crawl and emit JSON report.")
    parser.add_argument("url", help="Start URL, e.g. https://example.com")
    parser.add_argument(
        "--max-urls",
        type=int,
        default=DEFAULT_MAX_URLS,
        help="Max number of URLs to crawl (default: 20,000)",
    )
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Request timeout in seconds")
    parser.add_argument("--user-agent", default="seo-auditor-bot/0.1", help="Crawler user-agent")
    parser.add_argument("--output", default="seo_report.json", help="Output JSON path")
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="Ignore robots.txt disallow rules (use only when you have permission)",
    )
    parser.add_argument(
        "--feasibility-only",
        action="store_true",
        help="Print feasibility estimate for --max-urls and exit",
    )
    args = parser.parse_args()

    feasibility = estimate_feasibility(max(1, args.max_urls))
    if args.feasibility_only:
        print(json.dumps(feasibility, indent=2))
        return

    report = crawl(
        start_url=args.url,
        max_urls=max(1, args.max_urls),
        user_agent=args.user_agent,
        timeout=max(1, args.timeout),
        ignore_robots=args.ignore_robots,
    )

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print("Feasibility estimate:")
    print(json.dumps(feasibility, indent=2))
    print(f"Crawl completed. Report written to {args.output}")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
