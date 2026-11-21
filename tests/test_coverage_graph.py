"""Tests for coverage_graph: who originates a story and who amplifies it.

Small inline fixtures only -- no network. The run data never lives in the module.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import coverage_graph as cg  # noqa: E402


# ---------------------------------------------------------------- URL hygiene

class TestNormaliseUrl:
    def test_strips_tracking_params_and_fragment(self):
        u = "https://www.example.com/a/story?utm_source=x&utm_medium=y&id=7&fbclid=abc#comments"
        assert cg.normalize_url(u) == "https://example.com/a/story?id=7"

    def test_strips_amp_variants(self):
        assert cg.normalize_url("https://example.com/news/story/amp/") == "https://example.com/news/story"
        assert cg.normalize_url("https://example.com/news/story?amp=1") == "https://example.com/news/story"
        assert cg.normalize_url("https://amp.example.com/news/story.amp.html") == "https://example.com/news/story.html"
        assert cg.normalize_url(
            "https://www-example-com.cdn.ampproject.org/c/s/www.example.com/news/story"
        ) == "https://example.com/news/story"

    def test_arxiv_variants_collapse_to_abs_without_version(self):
        for u in ("https://arxiv.org/pdf/2611.01234v2", "http://export.arxiv.org/abs/2611.01234",
                  "https://www.arxiv.org/abs/2611.01234v1", "https://arxiv.org/html/2611.01234v3"):
            assert cg.normalize_url(u) == "https://arxiv.org/abs/2611.01234"

    def test_twitter_becomes_x_and_trailing_slash_dropped(self):
        assert cg.normalize_url("http://twitter.com/someone/status/1/") == "https://x.com/someone/status/1"

    def test_rejects_non_http(self):
        assert cg.normalize_url("mailto:a@b.c") is None
        assert cg.normalize_url("javascript:void(0)") is None
        assert cg.normalize_url("/relative/path") is None


class TestOutlet:
    def test_registrable_domain(self):
        assert cg.outlet_of("https://m.techcrunch.com/2026/11/01/x") == "techcrunch.com"
        assert cg.outlet_of("https://www.bbc.co.uk/news/x") == "bbc.co.uk"
        assert cg.outlet_of("https://blogs.nvidia.com/x") == "nvidia.com"

    def test_hosting_platforms_keep_the_publication(self):
        assert cg.outlet_of("https://importai.substack.com/p/x") == "importai.substack.com"
        assert cg.outlet_of("https://someone.github.io/post") == "someone.github.io"
        assert cg.outlet_of("https://medium.com/@alice/post-123") == "medium.com/@alice"

    def test_aliases(self):
        assert cg.outlet_of("https://twitter.com/a/status/1") == "x.com/a"
        assert cg.outlet_of("https://x.com/i/web/status/1") == "x.com"
        assert cg.outlet_of("https://deepmind.com/blog/x") == "deepmind.google"


# ---------------------------------------------------------------- extraction

ARTICLE_HTML = """<html><head>
<title>Lab ships memory | Example News</title>
<meta property="og:title" content="Lab ships long-term memory for agents">
<meta property="article:published_time" content="2026-11-03T10:00:00Z">
<meta name="author" content="By Jane Doe">
<link rel="canonical" href="https://example.com/2026/11/03/lab-memory?utm_source=rss">
</head><body>
<nav><a href="https://example.com/">Home</a><a href="https://othernews.com/top">Top</a></nav>
<article>
<p>The lab published <a href="https://arxiv.org/pdf/2611.00001v1">a paper</a> on memory.
As <a href="https://www.theinformation.com/articles/memory?utm_campaign=z">The Information first reported</a>,
the feature is coming. According to Bloomberg, the team grew.</p>
<p><a href="https://twitter.com/intent/tweet?url=x">Share</a> <a href="mailto:x@y.z">Mail</a>
<a href="https://example.com/tag/ai">AI tag</a> <a href="https://example.com/2026/10/01/older-story">Earlier coverage</a></p>
</article>
<footer><a href="https://facebook.com/example">fb</a></footer>
</body></html>"""


class TestExtractArticle:
    def test_metadata(self):
        a = cg.extract_article(ARTICLE_HTML, "https://example.com/2026/11/03/lab-memory")
        assert a["title"] == "Lab ships long-term memory for agents"
        assert a["published"] == "2026-11-03"
        assert a["author"] == "Jane Doe"
        assert a["canonical"] == "https://example.com/2026/11/03/lab-memory"

    def test_body_links_only_no_nav_share_tag_or_mail(self):
        a = cg.extract_article(ARTICLE_HTML, "https://example.com/2026/11/03/lab-memory")
        assert a["links"] == [
            "https://arxiv.org/abs/2611.00001",
            "https://theinformation.com/articles/memory",
            "https://example.com/2026/10/01/older-story",
        ]

    def test_jsonld_date_and_author(self):
        html = """<html><head><script type="application/ld+json">
        {"@context":"https://schema.org","@type":"NewsArticle","headline":"H",
         "datePublished":"2026-10-05T08:00:00+02:00","author":[{"@type":"Person","name":"Sam Roe"}]}
        </script></head><body><article><p>text</p></article></body></html>"""
        a = cg.extract_article(html, "https://x.org/a")
        assert a["published"] == "2026-10-05"
        assert a["author"] == "Sam Roe"

    def test_date_falls_back_to_url_then_arxiv_id(self):
        assert cg.extract_article("<html></html>", "https://x.org/2026/09/30/story")["published"] == "2026-09-30"
        assert cg.extract_article("<html></html>", "https://arxiv.org/abs/2610.01234")["published"] == "2026-10-01"

    def test_syndicated_copy_is_credited_to_the_original(self):
        html = '<html><head><link rel="canonical" href="https://origin.com/story"></head><body></body></html>'
        a = cg.extract_article(html, "https://finance.yahoo.com/news/story-123.html")
        assert a["outlet"] == "origin.com"
        assert a["syndicated_via"] == "yahoo.com"


def test_profile_links_are_not_citations():
    html = '<html><body><article><a href="https://x.org/team/jane">Jane</a><a href="https://x.org/users/9">u</a>' \
           '<a href="https://y.org/paper">paper</a></article></body></html>'
    assert cg.extract_article(html, "https://x.org/post")["links"] == ["https://y.org/paper"]


def test_arxiv_abs_page_keeps_only_abstract_links_and_infrastructure_hosts_are_dropped():
    html = ('<html><body><blockquote class="abstract">See <a href="https://github.com/org/repo">code</a> and '
            '<a href="https://www.google.com/search?q=x">search</a></blockquote>'
            '<a href="https://www.bibsonomy.org/BibtexHandler?x">bib</a></body></html>')
    assert cg.extract_article(html, "https://arxiv.org/abs/2611.00001")["links"] == ["https://github.com/org/repo"]


def test_open_substack_and_claude_aliases():
    assert cg.outlet_of("https://open.substack.com/pub/importai/p/x") == "importai.substack.com"
    assert cg.outlet_of("https://claude.com/blog/memory") == "anthropic.com"


def test_desk_bylines_are_not_people():
    assert cg.is_person("Jane Doe")
    for desk in ("AI Newsroom", "Grafy News Desk", "The Glonce editors", "editorialteam", "Amazon Web Services"):
        assert not cg.is_person(desk)


class TestMentions:
    def test_named_source_with_cue_is_found(self):
        text = "As The Information first reported, it ships. According to Bloomberg, staff grew."
        found = cg.extract_mentions(text, {"The Information": "theinformation.com", "Bloomberg": "bloomberg.com",
                                           "Reuters": "reuters.com"})
        assert found == ["bloomberg.com", "theinformation.com"]

    def test_name_without_cue_is_ignored(self):
        assert cg.extract_mentions("Bloomberg terminals are expensive.", {"Bloomberg": "bloomberg.com"}) == []


# ---------------------------------------------------------------- ranking

class TestPageRank:
    def test_matches_known_toy_graph(self):
        # a->b, b->c, c->a, d->c : c collects the most rank
        edges = {("a", "b"): 1, ("b", "c"): 1, ("c", "a"): 1, ("d", "c"): 1}
        pr = cg.pagerank(["a", "b", "c", "d"], edges)
        assert abs(sum(pr.values()) - 1) < 1e-9
        assert max(pr, key=pr.get) == "c"
        assert pr["d"] == pytest.approx(0.15 / 4, rel=1e-6)

    def test_agrees_with_networkx_when_installed(self):
        nx = pytest.importorskip("networkx")
        edges = {("a", "b"): 2, ("a", "c"): 1, ("b", "c"): 1, ("c", "a"): 1, ("d", "c"): 3, ("e", "a"): 1}
        g = nx.DiGraph()
        g.add_nodes_from("abcde")
        for (u, v), w in edges.items():
            g.add_edge(u, v, weight=w)
        ref = nx.pagerank(g, weight="weight")
        ours = cg.pagerank(list("abcde"), edges)
        for n in ref:
            assert ours[n] == pytest.approx(ref[n], abs=1e-6)


# ---------------------------------------------------------------- stories

def _art(i, title, date, outlet, links=()):
    return {"id": i, "title": title, "published": date, "outlet": outlet,
            "url": f"https://{outlet}/{i}", "links": list(links)}


class TestStories:
    def test_cluster_by_title_and_shared_link_and_earliest_source(self):
        paper = "https://arxiv.org/abs/2611.00001"
        arts = [
            _art("a1", "Lab unveils SoMem wiki memory for AI agents", "2026-11-10", "labblog.com"),
            _art("a2", "SoMem: AI agents keep their own wiki memory", "2026-11-12", "news1.com", [paper]),
            _art("a3", "Why agents need a wiki", "2026-11-13", "news2.com", [paper]),
            _art("a4", "Chip export rules tighten again", "2026-11-11", "news3.com"),
        ]
        clusters = cg.cluster_stories(arts)
        assert sorted(map(sorted, clusters)) == [["a1", "a2", "a3"], ["a4"]]
        origin = cg.story_origin([a for a in arts if a["id"] in ("a1", "a2", "a3")])
        assert origin["id"] == "a1"

    def test_common_title_words_and_internal_links_do_not_glue_stories(self):
        arts = [_art(f"c{i}", f"Claude Anthropic OpenAI {w} news", "2026-11-01", f"o{i}.com",
                     [f"https://o{i}.com/related/1"]) for i, w in enumerate(["pricing", "outage", "lawsuit",
                                                                              "hiring", "chips", "ads", "music"])]
        arts += [_art("s", "Unrelated", "2026-11-01", "o0.com", ["https://o0.com/related/1"])]
        assert len(cg.cluster_stories(arts)) == 8

    def test_generic_links_do_not_glue_stories(self):
        arts = [_art(f"g{i}", t, "2026-11-01", f"o{i}.com", ["https://github.com"])
                for i, t in enumerate(["Cats learn piano", "Mars rover finds ice", "Bond yields rise"])]
        assert len(cg.cluster_stories(arts)) == 3

    def test_origin_prefers_the_item_the_story_links_back_to(self):
        arts = [_art("early", "loosely matched", "2026-10-01", "a.com"),
                _art("src", "lab post", "2026-11-06", "lab.com"),
                _art("amp1", "coverage", "2026-11-07", "b.com", ["https://lab.com/src"]),
                _art("amp2", "coverage", "2026-11-08", "c.com", ["https://lab.com/src"])]
        assert cg.story_origin(arts)["id"] == "src"

    def test_origin_ignores_undated(self):
        arts = [_art("u", "x", "", "a.com"), _art("d", "x", "2026-10-02", "b.com")]
        assert cg.story_origin(arts)["id"] == "d"


# ---------------------------------------------------------------- end to end

def test_rank_end_to_end_marks_hub_vs_volume(tmp_path):
    arts = [
        {"id": "p", "url": "https://hub.org/p", "outlet": "hub.org", "title": "Hub paper on agent memory",
         "published": "2026-10-01", "author": "", "links": [], "mentions": [], "topics": ["memory"]},
    ]
    for i in range(4):
        arts.append({"id": f"v{i}", "url": f"https://volume.com/{i}", "outlet": "volume.com",
                     "title": f"Agent memory roundup {i}", "published": f"2026-10-0{i + 2}", "author": "Ann",
                     "links": ["https://hub.org/p"], "mentions": [], "topics": ["memory"]})
    arts.append({"id": "n", "url": "https://news.com/n", "outlet": "news.com", "title": "Memory story",
                 "published": "2026-10-09", "author": "", "links": ["https://hub.org/p"],
                 "mentions": [], "topics": ["memory"]})
    res = cg.rank(arts)
    top = res["outlets"][0]
    assert top["outlet"] == "hub.org"
    assert top["role"] == "originating hub"
    vol = next(o for o in res["outlets"] if o["outlet"] == "volume.com")
    assert vol["articles"] == 4 and vol["role"] == "volume driver"
    assert json.dumps(res)  # serialisable
