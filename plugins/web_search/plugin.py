"""Web search via DuckDuckGo Lite (no API key, no JS required).

OFFLINE GUARD: JARVIS defaults to offline-only (SecurityConfig.
internet_access=False). The web_search call is gated against that
flag — if internet access isn't enabled, the function returns an
explicit message *without* making the HTTPS request. To enable
internet for web search, set security.internet_access = true in
~/jarvis/config/jarvis.toml.
"""
import urllib.request
import urllib.parse
import html as htmllib
import re


async def web_search(query: str, max_results: int = 5) -> str:
    # Offline guard — the project ships offline-only by default.
    # Returning a clear message instead of a network call means the
    # caller (knowledge_curator, an LM tool call) sees the offline
    # state explicitly rather than a generic timeout error.
    try:
        from agent.core.config import config
        if not config.security.internet_access:
            return (
                "ERROR: web search disabled (offline mode). To enable, "
                "set security.internet_access = true in "
                "~/jarvis/config/jarvis.toml — JARVIS will only then "
                "make outbound HTTPS requests."
            )
    except Exception:
        # If config can't be loaded, fail closed: refuse the network call.
        return "ERROR: web search disabled (config unavailable; failing closed)."

    encoded = urllib.parse.quote_plus(query)
    url = f"https://lite.duckduckgo.com/lite/?q={encoded}"
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    req = urllib.request.Request(url, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        return f"ERROR: web search failed: {e}"

    # DDG Lite result links — href may come before or after class attribute
    link_pat = re.compile(
        r'<a\b[^>]*class=["\']result-link["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
        r'|<a\b[^>]*href=["\']([^"\']+)["\'][^>]*class=["\']result-link["\'][^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )
    snippet_pat = re.compile(
        r'<td[^>]*class=["\']result-snippet["\'][^>]*>(.*?)</td>',
        re.DOTALL | re.IGNORECASE,
    )

    raw_links = []
    for m in link_pat.finditer(body):
        href = m.group(1) or m.group(3)
        title = m.group(2) or m.group(4) or ""
        raw_links.append((href, title))

    snippets = [
        htmllib.unescape(re.sub(r"<[^>]+>", "", s)).strip()
        for s in snippet_pat.findall(body)
    ]

    if not raw_links:
        return f"No results found for: {query}"

    results = []
    for i, (href, title) in enumerate(raw_links[:max_results]):
        # Unwrap DDG redirect to get the actual URL
        m = re.search(r"uddg=([^&]+)", href)
        actual_url = urllib.parse.unquote(m.group(1)) if m else href
        title_clean = htmllib.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        snippet = snippets[i] if i < len(snippets) else ""
        results.append(f"{i+1}. {title_clean}\n   {actual_url}\n   {snippet}")

    return f"Search results for '{query}':\n\n" + "\n\n".join(results)
