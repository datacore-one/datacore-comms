#!/usr/bin/env python3
"""Coverage graph: which sources first carry a story, and which only amplify it.

Collect candidate articles for a topic set, fetch each one politely, pull its
publish date, outlet, author and the links in its body, then build who-cites-whom
graphs (article -> article, outlet -> outlet, author -> author) and rank them with
PageRank, in-degree and earliest appearance. Articles are grouped into stories; for
each story the earliest dated item is the origin and the rest are the amplification
chain.

    coverage_graph.py collect-hn --run DIR --topic memory --query "agent memory"
    coverage_graph.py run --run DIR          # fetch -> build -> expand -> rank -> report
    coverage_graph.py fetch|build|expand|rank|report --run DIR

The collect step for web/news search is done by an agent (search MCP tools are not
callable from Python): it writes DIR/collect/<anything>.jsonl, one object per line:
    {"url", "title", "published", "author", "topic", "source", "query"}

The module ships code only. All run data lives in the run directory you pass
(for PLUR: 5-plur/1-tracks/comms/coverage-graph/<run-date>/). Fetched pages are
untrusted data: they are parsed, never executed, and every string that reaches the
HTML graph is inserted as text, not markup. Nothing is posted or published.
"""
from __future__ import annotations

import argparse
import hashlib
import html as htmllib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

USER_AGENT = "coverage-graph/0.1"
MENTION_WEIGHT = 0.3  # "according to The Information" without a link
HN_WEIGHT = 0.5       # a Hacker News submission of the outlet's article

# ------------------------------------------------------------------ outlets

TRACKING_PARAMS = re.compile(
    r"^(utm_.*|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|ref|ref_src|ref_url|referrer|source|"
    r"cmpid|cmp|campaign|smid|smtyp|sh|s|src|share|sr_share|ito|guccounter|guce_.*|_hsenc|_hsmi|"
    r"mkt_tok|ncid|taid|soc_src|soc_trk|amp|outputtype|output|cid|trk|igshid|si|rss|feed|via|r)$", re.I)
HOST_PREFIXES = ("www.", "m.", "amp.", "mobile.")
TWO_PART_SUFFIXES = {"co.uk", "org.uk", "ac.uk", "gov.uk", "com.au", "net.au", "co.jp", "co.kr", "co.in",
                     "com.cn", "com.br", "com.sg", "com.hk", "co.nz", "co.za", "com.tr", "com.mx"}
PLATFORMS = {"substack.com", "github.io", "blogspot.com", "wordpress.com", "hashnode.dev", "beehiiv.com",
             "ghost.io", "vercel.app", "netlify.app", "notion.site", "pages.dev", "tumblr.com",
             "medium.com", "dev.to", "hf.co", "huggingface.co"}
PATH_PLATFORMS = {"medium.com", "dev.to", "x.com"}  # publication (or account) is the first path segment
ALIASES = {"twitter.com": "x.com", "deepmind.com": "deepmind.google", "export.arxiv.org": "arxiv.org",
           "youtu.be": "youtube.com", "redd.it": "reddit.com", "claude.com": "anthropic.com",
           "claude.ai": "anthropic.com", "chatgpt.com": "openai.com", "jack-clark.net": "importai.substack.com",
           "lspace.beehiiv.com": "latent.space"}
# Hosts whose links are infrastructure, profiles, licences or link shorteners -- never a citation.
NONCITE_OUTLETS = {"google.com", "substack.com", "medium.com", "beehiiv.com", "creativecommons.org", "bibsonomy.org",
                   "core.ac.uk", "gotit.pub", "sciencecast.org", "scixplorer.org", "wikipedia.org", "archive.org",
                   "bit.ly", "t.co", "lnkd.in", "buff.ly", "ow.ly", "tinyurl.com", "patreon.com", "ko-fi.com",
                   "buymeacoffee.com", "gravatar.com", "w3.org", "schema.org", "gstatic.com", "googleapis.com",
                   "list-manage.com", "mailchimp.com", "feedburner.com", "spotify.com", "apple.com", "paypal.com",
                   "elevenlabs.io", "orcid.org", "addthis.com", "disqus.com", "wp.com", "wordpress.org",
                   "gmpg.org", "feedly.com", "flipboard.com", "pocket.com", "getpocket.com", "calendly.com"}
SYNDICATORS = {"yahoo.com", "msn.com", "flipboard.com", "newsbreak.com", "ground.news", "biztoc.com",
               "news.google.com", "google.com", "inkl.com", "headtopics.com"}
SOCIAL = {"x.com", "linkedin.com", "facebook.com", "reddit.com", "news.ycombinator.com", "youtube.com",
          "threads.net", "bsky.app", "instagram.com", "tiktok.com", "t.me", "discord.gg", "mastodon.social"}

OUTLET_NAMES = {
    "theinformation.com": "The Information", "bloomberg.com": "Bloomberg", "reuters.com": "Reuters",
    "ft.com": "Financial Times", "wsj.com": "Wall Street Journal", "nytimes.com": "New York Times",
    "washingtonpost.com": "Washington Post", "cnbc.com": "CNBC", "techcrunch.com": "TechCrunch",
    "venturebeat.com": "VentureBeat", "the-decoder.com": "The Decoder", "semafor.com": "Semafor",
    "businessinsider.com": "Business Insider", "forbes.com": "Forbes", "technologyreview.com": "MIT Technology Review",
    "arstechnica.com": "Ars Technica", "zdnet.com": "ZDNet", "siliconangle.com": "SiliconANGLE",
    "theverge.com": "The Verge", "wired.com": "Wired", "axios.com": "Axios", "fortune.com": "Fortune",
    "economist.com": "The Economist", "theguardian.com": "The Guardian", "bbc.co.uk": "BBC", "bbc.com": "BBC",
    "cnn.com": "CNN", "time.com": "TIME", "theatlantic.com": "The Atlantic", "vox.com": "Vox",
    "politico.com": "Politico", "newscientist.com": "New Scientist", "spectrum.ieee.org": "IEEE Spectrum",
    "fastcompany.com": "Fast Company", "qz.com": "Quartz", "gizmodo.com": "Gizmodo", "engadget.com": "Engadget",
    "theregister.com": "The Register", "infoq.com": "InfoQ", "semianalysis.com": "SemiAnalysis",
    "epoch.ai": "Epoch AI", "metr.org": "METR", "nature.com": "Nature", "importai.substack.com": "Import AI",
    "jack-clark.net": "Import AI", "latent.space": "Latent Space", "deeplearning.ai": "The Batch (DeepLearning.AI)",
    "interconnects.ai": "Interconnects", "platformer.news": "Platformer", "stratechery.com": "Stratechery",
    "bensbites.com": "Ben's Bites", "tldr.tech": "TLDR", "news.ycombinator.com": "Hacker News",
    "lesswrong.com": "LessWrong", "alignmentforum.org": "Alignment Forum", "arxiv.org": "arXiv",
    "huggingface.co": "Hugging Face", "github.com": "GitHub", "x.com": "X (Twitter)", "reddit.com": "Reddit",
    "youtube.com": "YouTube", "linkedin.com": "LinkedIn", "openai.com": "OpenAI", "anthropic.com": "Anthropic",
    "deepmind.google": "Google DeepMind", "research.google": "Google Research", "blog.google": "Google",
    "meta.com": "Meta", "microsoft.com": "Microsoft", "amazon.com": "Amazon / AWS", "nvidia.com": "NVIDIA",
    "thezvi.substack.com": "Don't Worry About the Vase (Zvi)", "thezvi.wordpress.com": "Don't Worry About the Vase (Zvi)",
    "astralcodexten.com": "Astral Codex Ten", "dwarkesh.com": "Dwarkesh Podcast", "marktechpost.com": "MarkTechPost",
    "analyticsindiamag.com": "Analytics India Magazine", "letta.com": "Letta", "mem0.ai": "Mem0",
    "getzep.com": "Zep", "langchain.com": "LangChain", "sakana.ai": "Sakana AI", "36kr.com": "36Kr",
    "theneurondaily.com": "The Neuron", "aibase.com": "AIbase", "transformernews.ai": "Transformer",
    "ai-2027.com": "AI 2027", "understandingai.org": "Understanding AI", "garymarcus.substack.com": "Gary Marcus",
    "simonwillison.net": "Simon Willison", "every.to": "Every", "theaidigest.org": "AI Digest",
    "casp.ac": "CASP (Cambridge AI science and policy programme)", "aifutures.org": "AI Futures Project",
    "forecastingresearch.org": "Forecasting Research Institute", "aisi.gov.uk": "UK AI Security Institute",
    "noahpinion.blog": "Noahpinion", "thenextweb.com": "The Next Web", "9to5google.com": "9to5Google",
}
# Name -> outlet for unlinked mentions. Ambiguous one-word names (Time, Science,
# Transformer, Every) are left out on purpose: a cue word is not enough for them.
MENTION_NAMES = {n: d for d, n in OUTLET_NAMES.items()
                 if n not in {"TIME", "Every", "Transformer", "X (Twitter)", "GitHub", "YouTube", "LinkedIn",
                              "Reddit", "Google", "Meta", "Microsoft", "Amazon / AWS", "NVIDIA", "OpenAI",
                              "Anthropic", "Hugging Face", "Nature", "Letta", "Mem0", "Zep", "LangChain"}}
MENTION_NAMES.update({"FT": "ft.com", "WSJ": "wsj.com", "NYT": "nytimes.com", "the Times": "nytimes.com",
                      "The Batch": "deeplearning.ai", "TechCrunch": "techcrunch.com"})

KIND_RESEARCH = {"arxiv.org", "openreview.net", "aclanthology.org", "semanticscholar.org", "nature.com",
                 "science.org", "paperswithcode.com", "proceedings.mlr.press", "neurips.cc", "iclr.cc",
                 "researchgate.net", "ssrn.com", "alphaxiv.org", "huggingface.co", "doi.org", "zenodo.org",
                 "biorxiv.org", "techrxiv.org", "preprints.org", "arxiv.science", "acm.org", "springer.com",
                 "sciencedirect.com", "mdpi.com", "ieee.org", "papers.cool", "emergentmind.com", "aclweb.org"}
KIND_LAB = {"openai.com", "anthropic.com", "deepmind.google", "research.google", "blog.google", "meta.com",
            "microsoft.com", "amazon.com", "nvidia.com", "apple.com", "ibm.com", "letta.com", "mem0.ai",
            "getzep.com", "langchain.com", "sakana.ai", "perplexity.ai", "x.ai", "mistral.ai", "cohere.com",
            "thinkingmachines.ai", "epoch.ai", "metr.org", "redis.io", "upstash.com", "cognee.ai",
            "supermemory.ai", "salesforce.com", "databricks.com", "snowflake.com", "together.ai"}
KIND_NEWSLETTER = {"latent.space", "deeplearning.ai", "interconnects.ai", "platformer.news", "stratechery.com",
                   "bensbites.com", "tldr.tech", "jack-clark.net", "lesswrong.com", "alignmentforum.org",
                   "astralcodexten.com", "dwarkesh.com", "theneurondaily.com", "transformernews.ai",
                   "understandingai.org", "simonwillison.net", "every.to", "aiweekly.co", "ai-2027.com"}
KIND_INSTITUTE = {"casp.ac", "aifutures.org", "forecastingresearch.org", "aisi.gov.uk", "metr.org", "epoch.ai",
                  "forethought.org", "governance.ai", "rand.org", "brookings.edu", "cset.georgetown.edu",
                  "safe.ai", "far.ai", "redwoodresearch.org", "apolloresearch.ai", "openphilanthropy.org"}
KIND_NEWS = {d for d in OUTLET_NAMES if d not in KIND_RESEARCH | KIND_LAB | KIND_NEWSLETTER | SOCIAL | KIND_INSTITUTE
             | {"github.com"} and not d.endswith("substack.com")}
KIND_NEWS |= {"36kr.com", "aibase.com", "marktechpost.com", "analyticsindiamag.com", "pureai.com",
              "theaiinsider.tech", "techradar.com", "tomsguide.com", "techrepublic.com", "pcmag.com",
              "computerworld.com", "itpro.com", "datacenterdynamics.com", "techtarget.com", "thenewstack.io",
              "unite.ai", "artificialintelligence-news.com", "techopedia.com", "observer.com", "inc.com",
              "nbcnews.com", "apnews.com", "cbsnews.com", "npr.org", "independent.co.uk", "telegraph.co.uk",
              "scmp.com", "nikkei.com", "lemonde.fr", "spiegel.de", "heise.de", "theinformation.com",
              "thenextweb.com", "techfundingnews.com", "siliconrepublic.com", "geekwire.com", "betakit.com",
              "sifted.eu", "tech.eu", "eweek.com", "crn.com", "sdxcentral.com", "devops.com", "infoworld.com",
              "theaitrack.com", "winbuzzer.com", "neowin.net", "digitaltrends.com", "mashable.com", "pymnts.com",
              "benzinga.com", "investing.com", "seekingalpha.com", "fool.com", "barrons.com", "marketwatch.com",
              "cnet.com", "zdnet.com", "venturebeat.com", "hpcwire.com", "aiwire.net", "bdtechtalks.com"}


def _registrable(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in TWO_PART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _clean_host(host: str) -> str:
    host = (host or "").lower().strip(".")
    if host.endswith(".cdn.ampproject.org"):
        return host
    changed = True
    while changed:
        changed = False
        for p in HOST_PREFIXES:
            if host.startswith(p) and host.count(".") >= 2:
                host, changed = host[len(p):], True
    return ALIASES.get(host, host)


def normalize_url(url: str | None) -> str | None:
    """Canonical form of an absolute http(s) URL, or None for anything else."""
    if not url:
        return None
    url = url.strip()
    try:
        p = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    host = _clean_host(p.hostname or "")
    path = p.path or ""
    if host.endswith(".cdn.ampproject.org"):  # /c/s/www.example.com/path -> example.com/path
        m = re.match(r"^/(?:[a-z]/)*(?:s/)?(.+)$", path)
        return normalize_url("https://" + m.group(1)) if m else None
    m = re.match(r"^/pub/([a-z0-9\-]+)(/.*)?$", path) if host == "open.substack.com" else None
    if m:
        host, path = f"{m.group(1)}.substack.com", m.group(2) or ""
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
             if not TRACKING_PARAMS.match(k)]
    path = re.sub(r"/amp/?$", "", path)
    path = re.sub(r"\.amp\.html$", ".html", path)
    path = re.sub(r"/+", "/", path)
    if host == "arxiv.org":
        m = re.match(r"^/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+/[0-9]{7})(?:v\d+)?(?:\.pdf)?/?$", path)
        if m:
            return f"https://arxiv.org/abs/{m.group(1)}"
    path = path.rstrip("/")
    q = urllib.parse.urlencode(query)
    return f"https://{host}{path}" + (f"?{q}" if q else "")


def outlet_of(url: str | None) -> str:
    """The publication behind a URL: registrable domain, or the publication on a hosting platform."""
    u = normalize_url(url) if url and "://" in url else None
    if not u:
        return ""
    p = urllib.parse.urlsplit(u)
    host = p.hostname or ""
    reg = ALIASES.get(_registrable(host), _registrable(host))
    if reg in PATH_PLATFORMS:
        seg = [s for s in p.path.split("/") if s]
        return f"{reg}/{seg[0]}" if seg and seg[0] not in ("i", "intent", "search", "home", "m", "p", "tag") else reg
    if reg in PLATFORMS and host != reg:
        return host
    if host in OUTLET_NAMES:  # e.g. news.ycombinator.com, spectrum.ieee.org
        return host
    return reg


def outlet_name(outlet: str) -> str:
    return OUTLET_NAMES.get(outlet, outlet)


def outlet_kind(outlet: str) -> str:
    base = outlet.split("/")[0]
    if base in SOCIAL:
        return "social/forum"
    if base in ("github.com", "pypi.org", "npmjs.com", "gitlab.com", "modelcontextprotocol.io"):
        return "code"
    if base in KIND_RESEARCH:
        return "research"
    if base in KIND_INSTITUTE or base.endswith((".edu", ".gov", ".ac.uk", ".gov.uk", ".ac")):
        return "institute/policy"
    if base in KIND_LAB:
        return "lab/company"
    if base in KIND_NEWSLETTER or base.endswith((".substack.com", ".beehiiv.com", ".ghost.io")):
        return "newsletter/blog"
    if base in KIND_NEWS:
        return "news"
    return "other/blog"


# ------------------------------------------------------------------ extraction

def _iso(value) -> str:
    """YYYY-MM-DD from the usual date spellings, or ''."""
    if not value:
        return ""
    s = str(value).strip()
    m = re.search(r"(19|20)\d{2}-\d{2}-\d{2}", s)
    if m:
        return m.group(0)
    try:
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%Y/%m/%d", "%b. %d, %Y"):
        try:
            return datetime.strptime(s[:40].strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def date_from_url(url: str) -> str:
    m = re.search(r"/(20\d{2})[/-](\d{1,2})[/-](\d{1,2})(?:/|-|$|\.)", url or "")
    if m:
        y, mo, d = map(int, m.groups())
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return ""
    m = re.search(r"arxiv\.org/abs/(\d{2})(\d{2})\.\d{4,5}", url or "")
    if m and 1 <= int(m.group(2)) <= 12:
        return f"20{m.group(1)}-{m.group(2)}-01"
    return ""


def _jsonld_objects(soup):
    out = []
    for tag in soup.find_all("script", type=lambda t: t and "ld+json" in t):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                out.append(d)
                if "@graph" in d:
                    stack.append(d["@graph"])
    return out


def _person(v) -> str:
    if isinstance(v, list):
        names = [_person(x) for x in v]
        names = [n for n in names if n]
        return names[0] + (" et al." if len(names) > 2 else f" and {names[1]}" if len(names) == 2 else "") if names else ""
    if isinstance(v, dict):
        return str(v.get("name") or "")
    return str(v or "")


_NOT_PERSON = re.compile(r"(team|desk|editor|staff|newsroom|admin|news|writer|contributor|analytics|accounts|intelligence|\.ai\b|\.com\b|"
                         r"amazon web services|press|marketing|blog|research|lab\b|labs\b)", re.I)


def is_person(author: str) -> bool:
    """A byline that names a person, not a desk, team or organisation."""
    return bool(author) and not _NOT_PERSON.search(author) and len(author.split()) <= 6


def _clean_author(a: str) -> str:
    a = re.sub(r"\s+", " ", htmllib.unescape(a or "")).strip()
    a = re.sub(r"^(by|written by|posted by)\s*[:\-]?\s*", "", a, flags=re.I).strip(" ,|·-")
    if not a or "://" in a or a.startswith("@") and " " not in a or len(a) > 80:
        return ""
    return a


_SHARE = re.compile(r"(intent/|/share|sharer|shareArticle|/submit|submitlink|wa\.me|pinterest\.com/pin/create)", re.I)
_NONCITE_PATH = re.compile(r"/(tag|tags|category|categories|author|authors|topic|topics|section|newsletters?|"
                           r"subscribe|login|signin|signup|register|about|contact|privacy|terms|feed|rss|search|"
                           r"careers|jobs|cdn-cgi|wp-login|team|users?|people|profiles?|members?|contributors?)(/|$)", re.I)
_BODY_CLASS = re.compile(r"(article[-_]?body|articleBody|entry[-_]content|post[-_]content|post[-_]body|"
                         r"story[-_]body|content[-_]body|article__content|article-content|post__content|"
                         r"rich[-_]text|markdown|prose|body-text|single-post|blog-post)", re.I)
_JUNK_CLASS = re.compile(r"(share|social|related|newsletter|subscribe|promo|recommend|comment|footer|sidebar|"
                         r"advert|banner|cookie|signup|paywall|more-stories|read-next)", re.I)


def _body(soup, url: str = ""):
    if "arxiv.org/abs/" in (url or ""):  # the abs page is boilerplate apart from the abstract
        abstract = soup.find("blockquote", class_=re.compile("abstract"))
        if abstract:
            return abstract
    arts = soup.find_all("article")
    if arts:
        return max(arts, key=lambda t: len(t.get_text(" ", strip=True)))
    cand = soup.find(attrs={"itemprop": "articleBody"})
    if cand:
        return cand
    best = None
    for t in soup.find_all(["div", "section"], class_=_BODY_CLASS):
        if best is None or len(t.get_text(" ", strip=True)) > len(best.get_text(" ", strip=True)):
            best = t
    if best is not None:
        return best
    for name in ("main", "body"):
        t = soup.find(name)
        if t:
            return t
    return soup


def extract_article(html: str, url: str) -> dict:
    """Title, date, author, canonical, outlet and in-body outbound links of one page."""
    from bs4 import BeautifulSoup
    try:
        soup = BeautifulSoup(html or "", "lxml")
    except Exception:  # noqa: BLE001 -- lxml missing or choked: stdlib parser
        soup = BeautifulSoup(html or "", "html.parser")

    def meta(*keys):
        for k in keys:
            t = soup.find("meta", attrs={"property": k}) or soup.find("meta", attrs={"name": k}) \
                or soup.find("meta", attrs={"itemprop": k})
            if t and t.get("content"):
                return t["content"].strip()
        return ""

    ld = _jsonld_objects(soup)
    ld_art = next((o for o in ld if o.get("datePublished") or o.get("headline")), {})

    title = meta("og:title", "twitter:title", "citation_title") or ld_art.get("headline") or ""
    if not title and soup.title and soup.title.string:
        title = re.split(r"\s+[|–—-]\s+", soup.title.string.strip())[0]
    if not title and soup.find("h1"):
        title = soup.find("h1").get_text(" ", strip=True)
    title = re.sub(r"\s+", " ", htmllib.unescape(str(title))).strip()

    published = (_iso(meta("article:published_time", "og:published_time", "datePublished",
                           "parsely-pub-date", "sailthru.date", "pubdate", "publishdate", "date",
                           "dc.date", "DC.date.issued", "dcterms.created", "citation_publication_date",
                           "citation_date", "citation_online_date"))
                 or _iso(ld_art.get("datePublished") or ld_art.get("dateCreated")))
    if not published:
        t = soup.find("time", attrs={"datetime": True})
        published = _iso(t["datetime"]) if t else ""
    published = published or date_from_url(url)

    author = _clean_author(meta("author", "parsely-author", "sailthru.author", "dc.creator", "citation_author")
                           or _person(ld_art.get("author")))
    if not author:
        art_author = meta("article:author")
        author = _clean_author(art_author)
    if not author:
        t = soup.find("a", rel="author") or soup.find(class_=re.compile(r"byline|author-name", re.I))
        author = _clean_author(t.get_text(" ", strip=True)) if t else ""

    canon_tag = soup.find("link", rel=lambda r: r and "canonical" in (r if isinstance(r, list) else [r]))
    canonical = normalize_url(urllib.parse.urljoin(url, canon_tag["href"])) if canon_tag and canon_tag.get("href") else None
    if not canonical:
        canonical = normalize_url(meta("og:url"))

    fetched_outlet = outlet_of(url)
    outlet, via = fetched_outlet, ""
    if canonical and outlet_of(canonical) and outlet_of(canonical) != fetched_outlet:
        outlet, via = outlet_of(canonical), fetched_outlet

    body = _body(soup, url)
    for junk in body.find_all(["nav", "footer", "aside", "header", "form", "script", "style", "noscript", "figure"]):
        junk.decompose()
    for junk in body.find_all(class_=_JUNK_CLASS):
        if junk is not body and junk.decomposed is False:
            junk.decompose()
    self_urls = {normalize_url(url), canonical}
    links: list[str] = []
    for a in body.find_all("a", href=True):
        href = a["href"].strip()
        if _SHARE.search(href):
            continue
        u = normalize_url(urllib.parse.urljoin(url, href))
        if not u or u in self_urls or u in links:
            continue
        sp = urllib.parse.urlsplit(u)
        if sp.path in ("", "/") or _NONCITE_PATH.search(sp.path) or outlet_of(u).split("/")[0] in NONCITE_OUTLETS:
            continue
        links.append(u)
    text = re.sub(r"\s+", " ", body.get_text(" ", strip=True))[:30000]
    return {"title": title, "published": published, "author": author, "canonical": canonical,
            "outlet": outlet, "syndicated_via": via, "links": links, "text": text,
            "description": meta("og:description", "description")[:400]}


_CUE_BEFORE = re.compile(r"(according to|reported by|first reported|as reported|reporting (?:by|from|in)|"
                         r"report(?:ed)? (?:in|from|by)|told|per|via|cited by|wrote in|writing in|"
                         r"(?:story|report|article|interview|piece|newsletter|scoop) (?:in|from|by|with))"
                         r"\s+(?:the\s+)?$", re.I)
_CUE_AFTER = re.compile(r"^\W{0,2}\s*(?:first\s+|previously\s+|exclusively\s+|also\s+)?"
                        r"(reported|reports|wrote|writes|revealed|broke the news|scooped)\b", re.I)


def extract_mentions(text: str, names: dict | None = None) -> list[str]:
    """Outlets named as a source without a link ('according to Bloomberg')."""
    names = MENTION_NAMES if names is None else names
    found = set()
    for name, outlet in names.items():
        for m in re.finditer(r"(?<![\w])" + re.escape(name) + r"(?![\w])", text or ""):
            before = text[max(0, m.start() - 45):m.start()]
            after = text[m.end():m.end() + 30]
            if _CUE_BEFORE.search(before) or _CUE_AFTER.search(after):
                found.add(outlet)
                break
    return sorted(found)


# ------------------------------------------------------------------ ranking

def pagerank(nodes, edges: dict, damping: float = 0.85, tol: float = 1e-12, max_iter: int = 1000) -> dict:
    """Weighted PageRank by power iteration; dangling rank is spread evenly (as networkx does)."""
    order = list(dict.fromkeys(list(nodes) + [n for e in edges for n in e]))
    n = len(order)
    if not n:
        return {}
    out = defaultdict(float)
    for (u, _), w in edges.items():
        out[u] += w
    x = {k: 1.0 / n for k in order}
    for _ in range(max_iter):
        dangle = sum(x[k] for k in order if out[k] == 0)
        nxt = {k: (1 - damping) / n + damping * dangle / n for k in order}
        for (u, v), w in edges.items():
            nxt[v] += damping * x[u] * w / out[u]
        err = sum(abs(nxt[k] - x[k]) for k in order)
        x = nxt
        if err < n * tol:
            break
    return x


# ------------------------------------------------------------------ stories

STOP = set("""a an and are as at be by for from has have how in into is it its of on or that the their this to
was were what when where which who why will with without your you our we they them can could should would not
no yes vs via over under after before about than then just more most new now out up down off all any one two
three first last next get gets got make makes made use uses using used here there these those than been being do
does did doing own keep keeps also only very much many may might must need needs want wants like""".split())
GENERIC = set("""ai agent agents agentic memory memories llm llms model models language large learning learn learns
learned self improving improvement improve improves improved recursive recursively continual continually lifelong
long term longterm persistent research researchers paper papers study studies news week weekly issue update
updates launches launch launched introduces introducing introduced announces announced unveils unveiled release
releases released open source opensource future system systems intelligence artificial machine based toward towards
approach framework method methods data tool tools building build builds way ways era report reports says said
explained explainer guide 2025 2026 part podcast episode interview newsletter blog post today year years""".split())


def title_tokens(title: str) -> set[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-']+|\d+[A-Za-z]+[A-Za-z0-9]*", title or "")
    return {w.lower().strip("-'") for w in words
            if len(w) >= 3 and w.lower() not in STOP and w.lower() not in GENERIC}


def _anchor_ok(u: str) -> bool:
    """A link specific enough to tie two articles to the same story (a paper, a post, a repo)."""
    sp = urllib.parse.urlsplit(u)
    host = sp.hostname or ""
    depth = len([s for s in sp.path.split("/") if s])
    if sp.path in ("", "/") or host in SOCIAL and "/status/" not in sp.path and "/watch" not in sp.path:
        return False
    if outlet_of(u).split("/")[0] in NONCITE_OUTLETS:
        return False
    if host == "github.com":
        return depth >= 2
    return depth >= 1


def cluster_stories(articles: list[dict], title_jaccard: float = 0.34, link_df_cap: float = 0.15,
                    token_df_cap: float = 0.03) -> list[list]:
    """Group articles into stories: similar titles, or a shared specific link (an article's own URL
    counts as a link it shares with everyone who cites it). Union-find; returns lists of ids."""
    ids = [a["id"] for a in articles]
    parent = {i: i for i in ids}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    n = len(articles)
    toks = {a["id"]: title_tokens(a.get("title", "")) for a in articles}
    # words in more than ~3% of titles (Claude, OpenAI, ...) say nothing about which story it is
    tdf = Counter(t for ts in toks.values() for t in ts)
    common = {t for t, c in tdf.items() if c > max(5, token_df_cap * n)}
    toks = {i: ts - common for i, ts in toks.items()}
    # 1. title similarity (needs two shared non-generic words)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = toks[ids[i]], toks[ids[j]]
            inter = a & b
            if len(inter) >= 2 and len(inter) / len(a | b) >= title_jaccard:
                union(ids[i], ids[j])
    # 2. shared specific anchors. Single-link chaining over widely cited links grows one giant
    #    "story", so a shared link only joins two articles when it is what both of them lead
    #    with (one of their first two external links), or one article IS the link and the
    #    other leads with it.
    holders = defaultdict(set)
    own_of, lead = {}, {}
    for a in articles:
        own = {normalize_url(a.get("url")), normalize_url(a.get("canonical"))} - {None}
        external = [l for l in a.get("links") or [] if outlet_of(l) != a.get("outlet") and _anchor_ok(l)]
        lead[a["id"]] = set(external[:2])
        for u in set(external) | own:
            if u and _anchor_ok(u):
                holders[u].add(a["id"])
        for u in own:
            own_of[u] = a["id"]
    cap = max(10, int(link_df_cap * n))
    for u, hs in holders.items():
        if not 2 <= len(hs) <= cap:
            continue
        hs = sorted(hs)
        for x in range(len(hs)):
            for y in range(x + 1, len(hs)):
                i, j = hs[x], hs[y]
                if (u in lead[i] or own_of.get(u) == i) and (u in lead[j] or own_of.get(u) == j):
                    union(i, j)
    groups = defaultdict(list)
    for i in ids:
        groups[find(i)].append(i)
    return sorted(groups.values(), key=len, reverse=True)


def story_origin(cluster: list[dict], since: str = "") -> dict | None:
    """First public source of a story: the earliest dated item that other items in the story link to,
    or the earliest dated item when none is linked. Ties: most cited, then research/lab sources."""
    dated = [a for a in cluster if a.get("published") and a["published"] >= since]
    if not dated:
        return None
    urls = {}
    for a in cluster:
        for u in (a.get("url"), a.get("canonical")):
            if u:
                urls[normalize_url(u)] = a["id"]
    cites = Counter(urls[l] for a in cluster for l in set(a.get("links") or []) if l in urls and urls[l] != a["id"])
    kind_rank = {"research": 0, "lab/company": 1}
    # Prefer items the story itself points back to: an early article nobody in the story cites is
    # more likely a loosely matched neighbour than the source everyone picked the story up from.
    cited = [a for a in dated if cites[a["id"]] >= 1]
    pool = cited or dated
    return min(pool, key=lambda a: (a["published"], -cites[a["id"]], kind_rank.get(outlet_kind(a.get("outlet", "")), 2)))


# ------------------------------------------------------------------ rank

def _pct(values, q):
    if not values:
        return 0
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def rank(articles: list[dict], since: str = "") -> dict:
    """Outlet/author/story rankings from built articles. Pure function: easy to test.

    since: window start (YYYY-MM-DD). Items dated earlier (old papers pulled in because many
    articles cite them) stay in the graph, but cannot be a story's origin or an outlet's first
    appearance -- they are listed as background instead."""
    by_url = {}
    for a in articles:
        for u in (a.get("url"), a.get("canonical")):
            if u:
                by_url[normalize_url(u)] = a
    # --- outlet graph
    ow = defaultdict(float)          # (src, dst) -> weight
    kinds = defaultdict(set)         # (src, dst) -> {"link","mention","hn"}
    art_edges = []
    for a in articles:
        src = a["outlet"]
        targets = set()
        for l in a.get("links") or []:
            t = outlet_of(l)
            if t and t != src:
                targets.add(t)
            hit = by_url.get(l)
            if hit and hit["id"] != a["id"]:
                art_edges.append({"source": a["id"], "target": hit["id"], "kind": "link"})
        for t in targets:
            ow[(src, t)] += 1
            kinds[(src, t)].add("link")
        for t in set(a.get("mentions") or []) - targets - {src}:
            ow[(src, t)] += MENTION_WEIGHT
            kinds[(src, t)].add("mention")
        if a.get("hn_points") is not None and src != "news.ycombinator.com":
            ow[("news.ycombinator.com", src)] += HN_WEIGHT
            kinds[("news.ycombinator.com", src)].add("hn")
    outlets_in_sample = Counter(a["outlet"] for a in articles)
    nodes = set(outlets_in_sample) | {n for e in ow for n in e}
    pr = pagerank(sorted(nodes), dict(ow))

    citing = defaultdict(set)
    cited = defaultdict(set)
    for (s, t), w in ow.items():
        citing[t].add(s)
        cited[s].add(t)
    first_seen = {}
    for a in articles:
        if a.get("published") and a["published"] >= since:
            first_seen[a["outlet"]] = min(first_seen.get(a["outlet"], "9999"), a["published"])

    # --- stories
    clusters = cluster_stories(articles)
    by_id = {a["id"]: a for a in articles}
    stories, origin_count = [], Counter()
    for cl in clusters:
        members = [by_id[i] for i in cl]
        outlets = {m["outlet"] for m in members if not m.get("published") or m["published"] >= since}
        if len(members) < 2:
            continue
        origin = story_origin(members, since)
        if not origin:
            continue
        background = [{k: m.get(k, "") for k in ("title", "outlet", "published", "url")}
                      for m in members if m.get("published") and m["published"] < since]
        if len(outlets) >= 2:
            origin_count[origin["outlet"]] += 1
        member_urls = {}
        for m in members:
            for u in (m.get("url"), m.get("canonical")):
                if u:
                    member_urls[normalize_url(u)] = m
        timeline = []
        for m in sorted((m for m in members if not m.get("published") or m["published"] >= since),
                        key=lambda x: (x.get("published") or "9999", x["id"])):
            via = sorted({member_urls[l]["outlet"] for l in m.get("links") or []
                          if l in member_urls and member_urls[l]["id"] != m["id"]})
            timeline.append({"id": m["id"], "date": m.get("published", ""), "outlet": m["outlet"],
                             "title": m.get("title", ""), "url": m.get("url", ""), "author": m.get("author", ""),
                             "cites": via, "role": m.get("role", "search")})
        tok = Counter(t for m in members for t in title_tokens(m.get("title", "")))
        topics = sorted({t for m in members for t in m.get("topics") or []})
        lag = None
        dates = [m["published"] for m in members if m.get("published") and m["published"] >= since]
        if len(dates) >= 2:
            lag = (date.fromisoformat(max(dates)) - date.fromisoformat(min(dates))).days
        first = min((m for m in members if m.get("published") and m["published"] >= since), key=lambda m: m["published"])
        stories.append({"size": len(members), "outlets": len(outlets), "topics": topics,
                        "earliest": {k: first.get(k, "") for k in ("title", "outlet", "published", "url")},
                        "background": background,
                        "keywords": [t for t, _ in tok.most_common(5)],
                        "origin": {k: origin.get(k, "") for k in ("id", "title", "outlet", "published", "url", "author")},
                        "origin_kind": outlet_kind(origin["outlet"]), "span_days": lag, "timeline": timeline})
    stories.sort(key=lambda s: (s["outlets"], s["size"]), reverse=True)

    # --- outlet table + roles
    searched = Counter(a["outlet"] for a in articles if a.get("role", "search") == "search")
    vol_cut = max(3, _pct(list(searched.values()), 0.9))
    rows = []
    for o in nodes:
        n_art = outlets_in_sample.get(o, 0)
        n_search = searched.get(o, 0)
        n_citing = len(citing[o] - {o})
        n_out = len(cited[o] - {o})
        # hub: other outlets point at it more than it publishes (and it is pointed at a lot,
        # or it started a story); volume driver: publishes a lot, is pointed at less than it publishes
        if (n_citing >= 3 or (n_citing >= 2 and origin_count[o] >= 1)) and n_citing >= n_search:
            role = "originating hub"
        elif n_search >= vol_cut and n_citing < n_search:
            role = "volume driver"
        elif n_search >= 1 and n_out > n_citing:
            role = "amplifier"
        elif n_citing >= 1:
            role = "cited source"
        else:
            role = "other"
        rows.append({"outlet": o, "name": outlet_name(o), "kind": outlet_kind(o), "pagerank": round(pr[o], 6),
                     "articles": n_art,
                     "found_by_search": n_search,
                     "cited_by_outlets": n_citing, "cites_outlets": n_out,
                     "in_weight": round(sum(w for (s, t), w in ow.items() if t == o and s != o), 2),
                     "story_origins": origin_count[o], "first_seen": first_seen.get(o, ""), "role": role,
                     "topics": sorted({t for a in articles if a["outlet"] == o for t in a.get("topics") or []})})
    rows.sort(key=lambda r: (-r["pagerank"], r["outlet"]))

    # --- authors
    aw = defaultdict(float)
    for e in art_edges:
        sa, ta = by_id[e["source"]].get("author", ""), by_id[e["target"]].get("author", "")
        if is_person(sa) and is_person(ta) and sa != ta:
            aw[(sa, ta)] += 1
    authors = defaultdict(list)
    def _org_byline(a):  # "METR" on metr.org, "Hanging Context" on hangingcontext.com
        squashed = re.sub(r"[^a-z0-9]", "", a.get("author", "").lower())
        return bool(squashed) and (squashed in re.sub(r"[^a-z0-9]", "", a["outlet"].lower())
                                   or squashed in re.sub(r"[^a-z0-9]", "", outlet_name(a["outlet"]).lower()))

    for a in articles:
        if is_person(a.get("author", "")) and not _org_byline(a):
            authors[a["author"]].append(a)
    apr = pagerank(sorted(authors), dict(aw)) if authors else {}
    origin_ids = Counter(s["origin"]["id"] for s in stories if s["outlets"] >= 2)
    authors = {k: v for k, v in authors.items() if any(x.get("role", "search") == "search" for x in v)}
    author_rows = []
    for name, arts in authors.items():
        cited_by = set()
        for e in art_edges:
            if by_id[e["target"]].get("author") == name and by_id[e["source"]]["outlet"] not in {x["outlet"] for x in arts}:
                cited_by.add(by_id[e["source"]]["outlet"])
        author_rows.append({"author": name, "articles": len(arts), "outlets": sorted({x["outlet"] for x in arts}),
                            "topics": sorted({t for x in arts for t in x.get("topics") or []}),
                            "cited_by_outlets": len(cited_by), "story_origins": sum(origin_ids[x["id"]] for x in arts),
                            "pagerank": round(apr.get(name, 0), 6)})
    author_rows.sort(key=lambda r: (-r["articles"], -r["cited_by_outlets"], r["author"]))

    # --- how stories flow between kinds of source: who cites whom, and how many days later
    flow = defaultdict(float)
    for (s_, t), w in ow.items():
        if "hn" not in kinds[(s_, t)]:
            flow[(outlet_kind(s_), outlet_kind(t))] += w
    lags = defaultdict(list)
    for st in stories:
        if not st["origin"].get("published"):
            continue
        o_kind, o_day = st["origin_kind"], date.fromisoformat(st["origin"]["published"])
        firsts = {}
        for e in st["timeline"]:
            k = outlet_kind(e["outlet"])
            if e["date"] and e["outlet"] != st["origin"]["outlet"] and k not in firsts:
                firsts[k] = (date.fromisoformat(e["date"]) - o_day).days
        for k, d in firsts.items():
            if d >= 0:
                lags[(o_kind, k)].append(d)
    kind_flow = {"citations": [{"from": a, "to": b, "weight": round(w, 1)} for (a, b), w in
                               sorted(flow.items(), key=lambda kv: -kv[1])],
                 "origin_kinds": dict(Counter(st["origin_kind"] for st in stories if st["outlets"] >= 2)),
                 "first_pickup_days": [{"origin": a, "pickup": b, "stories": len(v), "median_days": sorted(v)[len(v) // 2]}
                                       for (a, b), v in sorted(lags.items(), key=lambda kv: -len(kv[1]))]}
    edges = [{"source": s, "target": t, "weight": round(w, 2), "kinds": sorted(kinds[(s, t)])}
             for (s, t), w in sorted(ow.items(), key=lambda kv: -kv[1])]
    return {"outlets": rows, "authors": author_rows, "stories": stories, "outlet_edges": edges, "kind_flow": kind_flow,
            "article_edges": art_edges, "counts": {"articles": len(articles), "outlets_in_sample": len(outlets_in_sample),
                                                   "outlet_nodes": len(nodes), "outlet_edges": len(edges),
                                                   "article_edges": len(art_edges), "stories": len(stories)}}


# ------------------------------------------------------------------ IO steps

def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def candidates(run: Path) -> list[dict]:
    """All collected candidates, deduplicated by normalised URL, topics merged."""
    merged: dict[str, dict] = {}
    for f in sorted((run / "collect").glob("*.jsonl")):
        for r in _read_jsonl(f):
            u = normalize_url(r.get("url"))
            if not u:
                continue
            c = merged.setdefault(u, {"url": u, "title": "", "published": "", "author": "", "topics": [],
                                      "sources": [], "role": "search"})
            for k in ("title", "published", "author"):
                if r.get(k) and not c[k]:
                    c[k] = _iso(r[k]) if k == "published" else str(r[k])
            if r.get("topic") and r["topic"] not in c["topics"]:
                c["topics"].append(r["topic"])
            src = r.get("source") or f.stem
            if src not in c["sources"]:
                c["sources"].append(src)
            if r.get("hn_points") is not None:
                c["hn_points"] = max(int(r["hn_points"]), int(c.get("hn_points") or 0))
            if src == "citation" and c["role"] == "search" and c["sources"] == [src]:
                c["role"] = "cited"
    return list(merged.values())


def _cache_path(run: Path, url: str) -> Path:
    return run / "cache" / (hashlib.sha1(url.encode()).hexdigest()[:20] + ".html")


def fetch_one(url: str, timeout: float = 20.0, max_bytes: int = 3_000_000) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 -- http(s) only, checked by normalize_url
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "xml" not in ctype:
            return r.status, r.geturl(), ""
        raw = r.read(max_bytes)
        charset = r.headers.get_content_charset() or "utf-8"
        return r.status, r.geturl(), raw.decode(charset, errors="replace")


def step_fetch(run: Path, delay: float = 1.0, limit: int | None = None, retry_failed: bool = False) -> dict:
    log_path = run / "fetch-log.jsonl"
    log = {r["url"]: r for r in _read_jsonl(log_path)}
    todo = [c["url"] for c in candidates(run)]
    done = ok = fail = 0
    for url in todo:
        if limit is not None and done >= limit:
            break
        cp = _cache_path(run, url)
        prev = log.get(url)
        if cp.exists() or (prev and not prev.get("ok") and not retry_failed):
            continue
        done += 1
        entry = {"url": url, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            status, final, body = fetch_one(url)
            entry.update(status=status, final_url=final, ok=bool(body))
            if body:
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(body, encoding="utf-8")
            else:
                entry["error"] = "not html"
        except urllib.error.HTTPError as e:
            entry.update(status=e.code, ok=False, error=f"HTTP {e.code}")
        except Exception as e:  # noqa: BLE001 -- any network failure is recorded, never fatal
            entry.update(status=0, ok=False, error=f"{type(e).__name__}: {str(e)[:120]}")
        log[url] = entry
        ok += entry["ok"]
        fail += not entry["ok"]
        _write_jsonl(log_path, log.values())
        print(f"[{done}] {'ok ' if entry['ok'] else 'ERR'} {url} {entry.get('error', '')}", file=sys.stderr)
        time.sleep(delay)
    return {"attempted": done, "ok": ok, "failed": fail, "cached_total": len(list((run / "cache").glob("*.html")))
            if (run / "cache").exists() else 0}


TOPIC_PATTERNS = {
    "memory": re.compile(r"\bmemor(y|ies)\b|long[- ]term context|remember(s|ing)? across", re.I),
    "continual": re.compile(r"continual(ly)?[- ]learn|self[- ]learning|lifelong learning|test[- ]time (training|learning)"
                            r"|learn(s|ing)? from (experience|feedback|mistakes)|self[- ]evolv|online learning"
                            r"|catastrophic forgetting|era of experience|self[- ]training|skill (evolution|learning|librar)"
                            r"|learn(s|ing)? on the job|continuous improvement", re.I),
    "rsi": re.compile(r"recursive(ly)?[- ]self[- ]improv|self[- ]improv|intelligence explosion|automat\w* ai research"
                      r"|\bRSI\b|AI R&D|rewrite their own (weights|code)|agents to build ai"
                      r"|automat\w* (ai )?r&d|ai research(er)? automation|automated (ai )?researcher|ai (that )?build(s|ing)? ai",
                      re.I),
}


def step_build(run: Path, window_days: int = 60, end: str | None = None) -> dict:
    end_d = date.fromisoformat(end) if end else date.fromisoformat(run.name) if re.match(r"\d{4}-\d{2}-\d{2}$", run.name) else date.today()
    start_d = end_d - timedelta(days=window_days)
    log = {r["url"]: r for r in _read_jsonl(run / "fetch-log.jsonl")}
    arts, dropped = [], Counter()
    for c in candidates(run):
        cp = _cache_path(run, c["url"])
        ex = {}
        if cp.exists():
            final = (log.get(c["url"]) or {}).get("final_url") or c["url"]
            ex = extract_article(cp.read_text(encoding="utf-8", errors="replace"), final)
        a = {"id": hashlib.sha1(c["url"].encode()).hexdigest()[:10], "url": c["url"],
             "canonical": ex.get("canonical") or "", "outlet": ex.get("outlet") or outlet_of(c["url"]),
             "syndicated_via": ex.get("syndicated_via", ""),
             "title": ex.get("title") or c["title"], "published": ex.get("published") or c["published"],
             "author": ex.get("author") or _clean_author(c["author"]), "links": ex.get("links", []),
             "mentions": [], "fetched": bool(ex), "role": c["role"], "sources": c["sources"],
             "topics": list(c["topics"]), "description": ex.get("description", "")}
        if c.get("hn_points") is not None:
            a["hn_points"] = c["hn_points"]
        text = ex.get("text", "")
        if text:
            a["mentions"] = sorted(set(extract_mentions(text)) - {a["outlet"]})
        for t, pat in TOPIC_PATTERNS.items():
            if t not in a["topics"] and (pat.search(a["title"]) or len(pat.findall(text[:8000])) >= 3):
                a["topics"].append(t)
        if a["role"] == "cited" and a["outlet"].split("/")[0] in NONCITE_OUTLETS | SOCIAL:
            dropped["cited infrastructure"] += 1
            continue
        if a["role"] == "search":
            if a["published"] and not (start_d.isoformat() <= a["published"] <= end_d.isoformat()):
                dropped["outside window"] += 1
                continue
            # Search-tool candidates were already picked for the topic; only keyword-search feeds
            # (Hacker News) bring in unrelated stories that happen to share a word.
            if a["sources"] == ["hn"] and a["fetched"] and not any(
                    p.search(a["title"] + " " + text[:8000]) for p in TOPIC_PATTERNS.values()):
                dropped["off topic (keyword feed)"] += 1
                continue
            if re.search(r"enhanced reader for ycombinator|connecting founders with angel", a["title"], re.I):
                dropped["aggregator page"] += 1
                continue
        arts.append(a)
    # an outlet's own URL can arrive twice (syndicated copy + original): keep the first by canonical
    seen, uniq = set(), []
    for a in arts:
        key = a["canonical"] or a["url"]
        if key in seen:
            dropped["duplicate canonical"] += 1
            continue
        seen.add(key)
        uniq.append(a)
    _write_jsonl(run / "articles.jsonl", uniq)
    return {"articles": len(uniq), "fetched": sum(a["fetched"] for a in uniq), "dropped": dict(dropped),
            "window": f"{start_d}..{end_d}"}


def step_expand(run: Path, min_cites: int = 2, max_new: int = 80) -> dict:
    """Queue external URLs cited by >= min_cites distinct outlets (papers, lab posts) as 'citation' candidates."""
    arts = _read_jsonl(run / "articles.jsonl")
    have = {a["url"] for a in arts} | {a["canonical"] for a in arts if a.get("canonical")}
    have |= {normalize_url(c["url"]) for c in candidates(run)}
    by = defaultdict(set)
    topics = defaultdict(Counter)
    for a in arts:
        for l in a.get("links") or []:
            if l not in have and outlet_of(l) != a["outlet"] and _anchor_ok(l) and outlet_of(l).split("/")[0] not in SOCIAL:
                by[l].add(a["outlet"])
                topics[l].update(a.get("topics") or [])
    picks = sorted((u for u, s in by.items() if len(s) >= min_cites), key=lambda u: -len(by[u]))[:max_new]
    rows = [{"url": u, "topic": topics[u].most_common(1)[0][0] if topics[u] else "", "source": "citation",
             "cited_by": len(by[u])} for u in picks]
    old = _read_jsonl(run / "collect" / "cited.jsonl")
    known = {r["url"] for r in old}
    _write_jsonl(run / "collect" / "cited.jsonl", old + [r for r in rows if r["url"] not in known])
    return {"queued": len([r for r in rows if r["url"] not in known]), "eligible": len(picks)}


def step_collect_hn(run: Path, topic: str, query: str, days: int = 60, min_points: int = 5, end: str | None = None) -> dict:
    """Hacker News stories (Algolia public API, no account) for a query within the window."""
    end_d = date.fromisoformat(end) if end else date.today()
    since = int(datetime.combine(end_d - timedelta(days=days), datetime.min.time(), tzinfo=timezone.utc).timestamp())
    q = urllib.parse.urlencode({"query": query, "tags": "story", "hitsPerPage": 100,
                                "numericFilters": f"created_at_i>{since},points>={min_points}"})
    req = urllib.request.Request(f"https://hn.algolia.com/api/v1/search?{q}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
        hits = json.loads(r.read().decode()).get("hits", [])
    rows = [{"url": h["url"], "title": h.get("title", ""), "published": (h.get("created_at") or "")[:10],
             "author": "", "topic": topic, "source": "hn", "query": query, "hn_points": h.get("points", 0),
             "hn_id": h.get("objectID")} for h in hits if h.get("url")]
    path = run / "collect" / f"hn-{topic}.jsonl"
    old = _read_jsonl(path)
    known = {normalize_url(r["url"]) for r in old}
    new = [r for r in rows if normalize_url(r["url"]) not in known]
    _write_jsonl(path, old + new)
    return {"hits": len(hits), "added": len(new)}


def step_rank(run: Path, window_days: int = 60, end: str | None = None) -> dict:
    end_d = date.fromisoformat(end) if end else date.fromisoformat(run.name) if re.match(r"\d{4}-\d{2}-\d{2}$", run.name) else date.today()
    res = rank(_read_jsonl(run / "articles.jsonl"), since=(end_d - timedelta(days=window_days)).isoformat())
    (run / "graph.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res["counts"]


# ------------------------------------------------------------------ report

def _md(s) -> str:
    return str(s or "").replace("|", "/").replace("\n", " ").strip()


def step_report(run: Path, top: int = 15) -> dict:
    g = json.loads((run / "graph.json").read_text(encoding="utf-8"))
    arts = _read_jsonl(run / "articles.jsonl")
    outs = g["outlets"]
    hubs = [o for o in outs if o["role"] == "originating hub"][:top]
    vol = sorted([o for o in outs if o["found_by_search"]], key=lambda o: (-o["found_by_search"], o["cited_by_outlets"]))[:top]
    L = [f"# Coverage graph -- run {run.name}", "",
         f"{g['counts']['articles']} articles from {g['counts']['outlets_in_sample']} outlets; "
         f"{g['counts']['outlet_nodes']} outlets in the citation graph, {g['counts']['outlet_edges']} outlet-to-outlet "
         f"edges, {g['counts']['article_edges']} article-to-article links, {g['counts']['stories']} multi-article stories.",
         f"Fetched and parsed: {sum(1 for a in arts if a.get('fetched'))}; metadata only (blocked or paywalled): "
         f"{sum(1 for a in arts if not a.get('fetched'))}. Found via citation (not search): "
         f"{sum(1 for a in arts if a.get('role') == 'cited')}.", "",
         "## Originating hubs (cited by two or more other outlets, high PageRank or first in a story)", "",
         "| # | Outlet | Kind | PageRank | Cited by outlets | Story origins | Articles in sample | First seen |",
         "|---|---|---|---|---|---|---|---|"]
    for i, o in enumerate(hubs, 1):
        L.append(f"| {i} | {_md(o['name'])} | {o['kind']} | {o['pagerank']:.4f} | {o['cited_by_outlets']} | "
                 f"{o['story_origins']} | {o['articles']} | {o['first_seen']} |")
    L += ["", "## Volume drivers (most in-window articles found by search)", "",
          "| # | Outlet | Kind | Articles found | Cited by outlets | Cites outlets | Role |", "|---|---|---|---|---|---|---|"]
    for i, o in enumerate(vol, 1):
        L.append(f"| {i} | {_md(o['name'])} | {o['kind']} | {o['found_by_search']} | {o['cited_by_outlets']} | "
                 f"{o['cites_outlets']} | {o['role']} |")
    ed = [o for o in outs if o["role"] == "originating hub" and o["kind"] not in ("research", "code", "social/forum")][:top]
    L += ["", "## Originating hubs you can pitch (papers, code hosts and social platforms left out)", "",
          "| # | Outlet | Kind | Cited by outlets | Articles found | Story origins | PageRank |", "|---|---|---|---|---|---|---|"]
    for i, o in enumerate(ed, 1):
        L.append(f"| {i} | {_md(o['name'])} | {o['kind']} | {o['cited_by_outlets']} | {o['found_by_search']} | "
                 f"{o['story_origins']} | {o['pagerank']:.4f} |")
    meta = {o["outlet"]: o for o in outs}
    for t in sorted({t for a in arts for t in a.get("topics") or []}):
        sub = [a for a in arts if t in (a.get("topics") or [])]
        found = Counter(a["outlet"] for a in sub if a.get("role", "search") == "search")
        citing = defaultdict(set)
        for a in sub:
            for o in {outlet_of(l) for l in a.get("links") or []} | set(a.get("mentions") or []):
                if o and o != a["outlet"]:
                    citing[o].add(a["outlet"])
        cand = sorted(set(found) | set(citing), key=lambda o: (-len(citing[o]), -found[o], o))
        L += ["", f"## Topic: {t} ({sum(found.values())} in-window articles found)", "",
              "| Outlet | Kind | Cited by outlets (this topic) | Articles found (this topic) | Overall role |",
              "|---|---|---|---|---|"]
        for o in cand[:15]:
            m = meta.get(o, {})
            L.append(f"| {_md(outlet_name(o))} | {outlet_kind(o)} | {len(citing[o])} | {found[o]} | {m.get('role', '')} |")
    kf = g.get("kind_flow") or {}
    L += ["", "## How stories move between kinds of source", "",
          "Citation weight from one kind of source to another (links count 1, unlinked mentions 0.3):", "",
          "| From | To | Weight |", "|---|---|---|"]
    for r in (kf.get("citations") or [])[:20]:
        L.append(f"| {r['from']} | {r['to']} | {r['weight']} |")
    L += ["", f"Where multi-outlet stories start: {', '.join(f'{k} {v}' for k, v in sorted((kf.get('origin_kinds') or {}).items(), key=lambda kv: -kv[1]))}", "",
          "Days from a story's origin to the first pickup by each kind of source (median):", "",
          "| Origin kind | Picked up by | Stories | Median days |", "|---|---|---|---|"]
    for r in (kf.get("first_pickup_days") or [])[:20]:
        L.append(f"| {r['origin']} | {r['pickup']} | {r['stories']} | {r['median_days']} |")
    L += ["", "## Biggest stories (origin -> amplifiers)", ""]
    for i, s in enumerate(g["stories"][:10], 1):
        o = s["origin"]
        bg = f"  \nBackground sources from before the window: {', '.join(outlet_name(b['outlet']) + ' ' + b['published'] for b in s['background'][:4])}" if s.get("background") else ""
        L += [f"### {i}. {_md(o['title'])[:110]}",
              f"Origin: **{_md(outlet_name(o['outlet']))}** ({s['origin_kind']}), {o['published']} -- {o['url']}  ",
              f"{s['size']} items, {s['outlets']} outlets, spread over {s['span_days']} days; topics: {', '.join(s['topics'])}{bg}", ""]
        for e in s["timeline"][:14]:
            via = f" (links to {', '.join(outlet_name(v) for v in e['cites'])})" if e["cites"] else ""
            L.append(f"- {e['date'] or 'undated'} -- {_md(outlet_name(e['outlet']))}: {_md(e['title'])[:90]}{via}")
        if len(s["timeline"]) > 14:
            L.append(f"- ... and {len(s['timeline']) - 14} more")
        L.append("")
    L += ["## Recurring authors", "", "| Author | Articles | Outlets | Cited by outlets | Story origins |", "|---|---|---|---|---|"]
    for a in [a for a in g["authors"] if a["articles"] >= 2][:25]:
        L.append(f"| {_md(a['author'])} | {a['articles']} | {_md(', '.join(outlet_name(x) for x in a['outlets']))} | "
                 f"{a['cited_by_outlets']} | {a['story_origins']} |")
    (run / "summary.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    write_html(run, g, arts)
    return {"summary": str(run / "summary.md"), "html": str(run / "graph.html")}


HTML_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Coverage graph</title>
<style>
:root{--bg:#fbfaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#d9d6cf;--card:#fff;--acc:#2f5d8a}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--fg:#ecebe6;--mut:#a09f99;--line:#3a3936;--card:#1f1f1d;--acc:#8fb6e0}}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:14px 18px;border-bottom:1px solid var(--line)}h1{font-size:17px;margin:0 0 4px}
.sub{color:var(--mut);font-size:12.5px}.wrap{display:grid;grid-template-columns:1fr 360px;height:calc(100vh - 64px)}
#g{width:100%;height:100%;display:block;cursor:grab}aside{border-left:1px solid var(--line);overflow:auto;padding:12px 14px;background:var(--card)}
.legend span{display:inline-block;margin:0 10px 4px 0;font-size:12px}.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px;vertical-align:-1px}
h2{font-size:14px;margin:14px 0 6px}ul{padding-left:16px;margin:4px 0}li{margin:3px 0}a{color:var(--acc)}
.tabs button{font:inherit;border:1px solid var(--line);background:transparent;color:var(--fg);padding:3px 9px;border-radius:5px;cursor:pointer;margin-right:4px}
.tabs button.on{background:var(--acc);color:var(--bg)}input{font:inherit;width:100%;padding:5px 7px;border:1px solid var(--line);border-radius:5px;background:var(--bg);color:var(--fg);margin:6px 0}
@media (max-width:800px){.wrap{grid-template-columns:1fr;height:auto}#g{height:60vh}aside{border-left:0;border-top:1px solid var(--line)}}
</style></head><body>
<header><h1>Coverage graph</h1><div class="sub" id="sub"></div></header>
<div class="wrap"><svg id="g"></svg><aside>
<div class="tabs"><button id="t1" class="on">Outlets</button><button id="t2">Stories</button></div>
<input id="q" placeholder="Filter outlets..."><div class="legend" id="legend"></div><div id="panel"></div></aside></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const COL={"research":"#c0392b","lab/company":"#8e44ad","institute/policy":"#16a085","newsletter/blog":"#d68910","news":"#2e86c1","social/forum":"#7f8c8d","code":"#27ae60","other/blog":"#a6a29a"};
const $=id=>document.getElementById(id), el=(t,txt,attrs)=>{const e=document.createElement(t);if(txt!=null)e.textContent=txt;Object.assign(e,attrs||{});return e};
$('sub').textContent=D.counts.articles+' articles, '+D.counts.outlet_nodes+' outlets, '+D.counts.outlet_edges+' citation edges. Node size = PageRank, arrow = cites. Run '+D.run+'. Local file, not published.';
const lg=$('legend');Object.entries(COL).forEach(([k,c])=>{const s=el('span');s.appendChild(el('i',null,{className:'dot'})).style.background=c;s.appendChild(document.createTextNode(k));lg.appendChild(s)});
const svg=$('g'),NS='http://www.w3.org/2000/svg';const W=()=>svg.clientWidth||800,H=()=>svg.clientHeight||600;
const nodes=D.nodes.map((n,i)=>({...n,x:W()/2+Math.cos(i*2.4)*(40+3*i),y:H()/2+Math.sin(i*2.4)*(40+3*i),vx:0,vy:0}));
const idx=new Map(nodes.map((n,i)=>[n.id,i]));const links=D.edges.filter(e=>idx.has(e.source)&&idx.has(e.target)).map(e=>({...e,s:nodes[idx.get(e.source)],t:nodes[idx.get(e.target)]}));
const maxPr=Math.max(...nodes.map(n=>n.pagerank));const R=n=>4+22*Math.sqrt(n.pagerank/maxPr);
const view=document.createElementNS(NS,'g');svg.appendChild(view);
const defs=document.createElementNS(NS,'defs');defs.innerHTML='<marker id="ar" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="5" markerHeight="5" orient="auto"><path d="M0,0L10,5L0,10z" fill="#999"/></marker>';svg.appendChild(defs);
const lineEls=links.map(l=>{const e=document.createElementNS(NS,'line');e.setAttribute('stroke','#9995');e.setAttribute('stroke-width',Math.min(4,0.6+l.weight/2));e.setAttribute('marker-end','url(#ar)');view.appendChild(e);return e});
const nodeEls=nodes.map(n=>{const g=document.createElementNS(NS,'g');const c=document.createElementNS(NS,'circle');c.setAttribute('r',R(n));c.setAttribute('fill',COL[n.kind]||'#999');c.setAttribute('stroke','#fff');g.appendChild(c);
 if(R(n)>9){const t=document.createElementNS(NS,'text');t.textContent=n.name;t.setAttribute('font-size','10.5');t.setAttribute('fill','currentColor');t.setAttribute('x',R(n)+2);t.setAttribute('y',3);g.appendChild(t)}
 const ti=document.createElementNS(NS,'title');ti.textContent=n.name+' ('+n.role+')';g.appendChild(ti);g.style.cursor='pointer';g.addEventListener('click',ev=>{ev.stopPropagation();showOutlet(n)});view.appendChild(g);return g});
let alpha=1;function tick(){const w=W(),h=H();for(let i=0;i<nodes.length;i++){const a=nodes[i];for(let j=i+1;j<nodes.length;j++){const b=nodes[j];let dx=a.x-b.x,dy=a.y-b.y,d2=Math.max(dx*dx+dy*dy,25);if(d2>160000)continue;const d=Math.sqrt(d2),f=Math.min(8,1800/d2);a.vx+=dx/d*f;a.vy+=dy/d*f;b.vx-=dx/d*f;b.vy-=dy/d*f}}
 links.forEach(l=>{const dx=l.t.x-l.s.x,dy=l.t.y-l.s.y,d=Math.sqrt(dx*dx+dy*dy)||1,f=Math.max(-6,Math.min(6,(d-110)*0.01));l.s.vx+=dx/d*f;l.s.vy+=dy/d*f;l.t.vx-=dx/d*f;l.t.vy-=dy/d*f});
 nodes.forEach(n=>{n.vx+=(w/2-n.x)*0.004;n.vy+=(h/2-n.y)*0.004;n.vx=Math.max(-20,Math.min(20,n.vx));n.vy=Math.max(-20,Math.min(20,n.vy));n.x+=n.vx*alpha;n.y+=n.vy*alpha;n.vx*=0.8;n.vy*=0.8});
 links.forEach((l,i)=>{const dx=l.t.x-l.s.x,dy=l.t.y-l.s.y,d=Math.sqrt(dx*dx+dy*dy)||1,r=R(l.t)+2;const e=lineEls[i];e.setAttribute('x1',l.s.x);e.setAttribute('y1',l.s.y);e.setAttribute('x2',l.t.x-dx/d*r);e.setAttribute('y2',l.t.y-dy/d*r)});
 nodes.forEach((n,i)=>nodeEls[i].setAttribute('transform','translate('+n.x+','+n.y+')'));alpha*=0.995;if(alpha>0.03)requestAnimationFrame(tick)}
tick();let sc=1,tx=0,ty=0;const apply=()=>view.setAttribute('transform','translate('+tx+','+ty+') scale('+sc+')');
svg.addEventListener('wheel',e=>{e.preventDefault();const k=e.deltaY<0?1.1:0.9;sc*=k;apply()},{passive:false});
let drag=null;svg.addEventListener('mousedown',e=>drag=[e.clientX-tx,e.clientY-ty]);window.addEventListener('mouseup',()=>drag=null);window.addEventListener('mousemove',e=>{if(drag){tx=e.clientX-drag[0];ty=e.clientY-drag[1];apply()}});
function list(items){const u=el('ul');items.forEach(x=>u.appendChild(x));return u}
function artLi(a){const li=el('li');li.appendChild(document.createTextNode((a.published||'undated')+' '));const l=el('a',a.title||a.url,{href:a.url,target:'_blank',rel:'noopener noreferrer'});li.appendChild(l);if(a.author)li.appendChild(document.createTextNode(' - '+a.author));return li}
function showOutlet(n){const p=$('panel');p.replaceChildren();p.appendChild(el('h2',n.name));
 p.appendChild(el('div',n.kind+' | '+n.role+' | PageRank '+n.pagerank.toFixed(4)+' | cited by '+n.cited_by_outlets+' outlets | cites '+n.cites_outlets+' | '+n.articles+' articles | story origins '+n.story_origins,{className:'sub'}));
 const inc=links.filter(l=>l.t===n).sort((a,b)=>b.weight-a.weight).map(l=>el('li',l.s.name+' ('+l.weight+', '+l.kinds.join('/')+')'));const out=links.filter(l=>l.s===n).sort((a,b)=>b.weight-a.weight).map(l=>el('li',l.t.name+' ('+l.weight+')'));
 p.appendChild(el('h2','Cited by'));p.appendChild(list(inc.slice(0,30)));p.appendChild(el('h2','Cites'));p.appendChild(list(out.slice(0,30)));
 p.appendChild(el('h2','Articles'));p.appendChild(list((D.articles[n.id]||[]).map(artLi)));
 nodeEls.forEach((g,i)=>g.style.opacity=(nodes[i]===n||links.some(l=>(l.s===n&&l.t===nodes[i])||(l.t===n&&l.s===nodes[i])))?1:0.25);
 lineEls.forEach((e,i)=>e.style.opacity=(links[i].s===n||links[i].t===n)?1:0.1)}
function showTop(filter){const p=$('panel');p.replaceChildren();p.appendChild(el('h2','Outlets by PageRank'));
 p.appendChild(list(nodes.filter(n=>!filter||n.name.toLowerCase().includes(filter)).sort((a,b)=>b.pagerank-a.pagerank).slice(0,60).map(n=>{const li=el('li');const a=el('a',n.name,{href:'#'});a.onclick=e=>{e.preventDefault();showOutlet(n)};li.appendChild(a);li.appendChild(document.createTextNode(' - '+n.role+', cited by '+n.cited_by_outlets+', '+n.articles+' articles'));return li})))}
function showStories(){const p=$('panel');p.replaceChildren();D.stories.forEach((s,i)=>{p.appendChild(el('h2',(i+1)+'. '+s.origin.title));
 p.appendChild(el('div','Origin: '+s.origin_name+' ('+s.origin_kind+'), '+s.origin.published+' | '+s.outlets+' outlets, '+s.span_days+' days',{className:'sub'}));
 p.appendChild(list(s.timeline.slice(0,20).map(e=>{const li=el('li');li.appendChild(document.createTextNode((e.date||'undated')+' '+e.outlet_name+': '));li.appendChild(el('a',e.title||e.url,{href:e.url,target:'_blank',rel:'noopener noreferrer'}));if(e.cites.length)li.appendChild(document.createTextNode(' (links to '+e.cites.join(', ')+')'));return li})))})}
svg.addEventListener('click',()=>{nodeEls.forEach(g=>g.style.opacity=1);lineEls.forEach(e=>e.style.opacity=1)});
$('q').addEventListener('input',e=>showTop(e.target.value.toLowerCase()));
$('t1').onclick=()=>{$('t1').className='on';$('t2').className='';showTop('')};$('t2').onclick=()=>{$('t2').className='on';$('t1').className='';showStories()};
showTop('');
</script></body></html>"""


def write_html(run: Path, g: dict, arts: list[dict], max_nodes: int = 160) -> Path:
    keep = [o for o in g["outlets"] if o["articles"] or o["cited_by_outlets"] >= 2][:max_nodes]
    ids = {o["outlet"] for o in keep}
    by_outlet = defaultdict(list)
    for a in sorted(arts, key=lambda a: a.get("published") or ""):
        if a["outlet"] in ids:
            by_outlet[a["outlet"]].append({k: a.get(k, "") for k in ("title", "url", "published", "author")})
    stories = []
    for s in g["stories"][:25]:
        s = dict(s)
        s["origin_name"] = outlet_name(s["origin"]["outlet"])
        s["timeline"] = [dict(e, outlet_name=outlet_name(e["outlet"]), cites=[outlet_name(c) for c in e["cites"]])
                         for e in s["timeline"]]
        stories.append(s)
    data = {"run": run.name, "counts": g["counts"],
            "nodes": [{"id": o["outlet"], **{k: o[k] for k in ("name", "kind", "pagerank", "articles", "cited_by_outlets",
                                                                 "cites_outlets", "story_origins", "role")}} for o in keep],
            "edges": [e for e in g["outlet_edges"] if e["source"] in ids and e["target"] in ids],
            "articles": by_outlet, "stories": stories}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/").replace("<!--", "<\\!--")
    out = run / "graph.html"
    out.write_text(HTML_TEMPLATE.replace("__DATA__", blob), encoding="utf-8")
    return out


# ------------------------------------------------------------------ CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "build", "expand", "rank", "report", "run", "collect-hn"):
        p = sub.add_parser(name)
        p.add_argument("--run", required=True, type=Path, help="run directory (data lives here, never in the module)")
        p.add_argument("--window-days", type=int, default=60)
        p.add_argument("--end", help="window end date YYYY-MM-DD (default: run folder name or today)")
        if name in ("fetch", "run"):
            p.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
            p.add_argument("--limit", type=int)
            p.add_argument("--retry-failed", action="store_true")
        if name in ("expand", "run"):
            p.add_argument("--min-cites", type=int, default=2)
            p.add_argument("--max-new", type=int, default=80)
        if name == "collect-hn":
            p.add_argument("--topic", required=True)
            p.add_argument("--query", required=True)
            p.add_argument("--min-points", type=int, default=5)
    a = ap.parse_args(argv)
    run = a.run.resolve()
    run.mkdir(parents=True, exist_ok=True)
    out = {}
    if a.cmd == "collect-hn":
        end = a.end or (run.name if re.match(r"\d{4}-\d{2}-\d{2}$", run.name) else None)
        out = step_collect_hn(run, a.topic, a.query, a.window_days, a.min_points, end)
    if a.cmd in ("fetch", "run"):
        out["fetch"] = step_fetch(run, a.delay, a.limit, a.retry_failed)
    if a.cmd in ("build", "run"):
        out["build"] = step_build(run, a.window_days, a.end)
    if a.cmd in ("expand", "run"):
        out["expand"] = step_expand(run, a.min_cites, a.max_new)
    if a.cmd == "run" and out["expand"]["queued"]:
        out["fetch_cited"] = step_fetch(run, a.delay, None, a.retry_failed)
        out["build"] = step_build(run, a.window_days, a.end)
    if a.cmd in ("rank", "run"):
        out["rank"] = step_rank(run, a.window_days, a.end)
    if a.cmd in ("report", "run"):
        out["report"] = step_report(run)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
