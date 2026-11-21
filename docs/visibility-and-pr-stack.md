# Visibility and PR stack: where the external skills run

The comms pipeline (Position → Plan → Generate → Enforce → Verify → Schedule → Analyze) now has four
external skill sets plugged into it. This page says which step each one runs in, what it reads, what it
writes, and what it may never do. The commands link here instead of repeating it.

| Skill set | What it is | Install | Check it works |
|---|---|---|---|
| **Newsjack** (elvisun/newsjack, MIT) | 35 PR and AI-visibility skills plus a local monitoring CLI | `curl -fsSL newsjack.sh \| sh` with `NEWSJACK_RUNTIMES=claude NEWSJACK_RUN_SETUP=0` (or review `install.sh` first) | `~/.newsjack/bin/newsjack --version` and `newsjack doctor` |
| **Claude SEO** (AgriciDaniel/claude-seo, MIT) | 29 SEO / GEO / agent-readiness skills and 20 agents | `claude plugin marketplace add AgriciDaniel/claude-seo` then `claude plugin install claude-seo@agricidaniel-claude-seo`, then `claude-seo setup` | `claude-seo doctor` shows Runtime ready and Chromium ready |
| **Coverage graph** (`lib/coverage_graph.py`, this module) | Who-cites-whom graph of news on a topic; PageRank finds where a narrative starts and who amplifies it | ships with the module | `pytest tests/test_coverage_graph.py` |
| **AEO page checklist** (below) | 11 checks that decide whether an AI agent can read and recommend a page | — | run as part of the monthly audit |

Newsjack works without a Medialyst account (RSS, X and web search instead of its live news index).
`pr-calendar`, `find-journalists` and the live `news-search` index need one; creating that account is the
owner's call, never an agent's.

## Where each skill runs

### Quarterly (`/campaign-plan`)
- `pr-strategist`: audience → positioning → news pegs → drumbeat, before any channel plan.
- `build-ai-visibility-panel`: the versioned prompt panel the quarter is measured against (which questions
  we want answer engines to answer with us). Its panel-selection gate is the owner's.
- Claude SEO `/seo plan saas` for the site plan; `/seo cluster` for the topic clusters the blog fills.

### Monthly (`/monthly-plan`)
- **Inputs before the calendar is drafted:**
  - the latest coverage-graph run: which sources originate stories in our beats, and who amplifies them;
  - the AI-visibility panel's content gaps: questions where we have no page that could be cited;
  - `pr-calendar` moments, only if the Medialyst account exists.
- **Audit:**
  - Claude SEO `/seo technical`, `/seo schema`, `/seo geo` and `/seo agentic` on the product site;
  - `/seo drift compare` against last month's baseline;
  - the AEO page checklist on every landing and integration page.
  - Findings become site tasks; agents never deploy them.

### Weekly (the distribution loop's Monday research)
- `newsjack-detector` over the last 72 hours, freshness-gated through `story-origin-check`, routed by
  `newsjack-triage`. Pitch-ready items go to `angle-generator`. Big stories always surface, even off-brief.
- `coverage_graph.py` re-run on the beats. Week-on-week, a new origin hub is a pitch target. An outlet that
  only amplifies is a placement target, not a first pitch.
- `news-search` for anything the drafters need dated evidence for.

### Per draft (before anything reaches the owner's board, inside `/approve-content`)
1. `fact-check`: every factual claim verified and cited. A failed claim blocks the draft.
2. `meanest-editor` for pitches, press releases and open letters; `newsworthiness-check` for anything sold as
   news. Both score; the scores go on the board row.
3. `ai-visibility-writing` for web pages and long posts, so answer engines can lift a clean answer.
4. `voice-extractor` fingerprint check against the sender's real writing, once a fingerprint exists.
5. Then the existing gates: voice enforcer, STEPPS (8 of 11), claim sheets, URL check.

### Video (`/demo-video`)
The agent edit loop: pull frames with ffmpeg, keep the strongest moments, put the best shot in the first
2 seconds, cut pauses, captions at the top, and print every cut with its timestamps so a person can change
any of them. See that command's Phase 0.

### Reactive
- `reactive-comment` for inbound journalist source requests; `quote-forecast` before a scheduled moment
  (keynote, earnings, IPO day); `crisis-holding` only with legal counsel in the loop.

## The AEO page checklist

A page passes only if an agent reading the raw HTML (no JavaScript) can answer "what is it, who is it for,
what does it do, and is it current".

1. **Accessibility tree:** icon buttons have `aria-label`, form fields have a `<label>`, images have
   descriptive `alt`, and no key fact lives only in an image.
2. **Facts in raw HTML:** product facts, numbers and install lines are in the HTML the server sends, not
   injected by a script.
3. **The reader's question first:** the reader's main question, in their words, is the first H2 under the H1.
4. **An "it depends" answer:** at most 100 words recommending by use case, including when an alternative is
   the better choice.
5. **A definition, not a slogan:** one sentence saying what it is, who it is for and what it does.
6. **Plain-text proof:** testimonials and proof as selectable text, not images.
7. **Dated facts:** an "as of <date>" on versions, benchmark figures and counts.
8. **Crawler access:** robots.txt allows the answer-engine crawlers we want (meta-webindexer, GPTBot,
   OAI-SearchBot, ClaudeBot, PerplexityBot, Google-Extended), and an `llms.txt` exists.
9. **No broken links:** no broken internal or outbound links.
10. **Semantic HTML:** `nav`, `header`, `main`, `button` and `a`, not clickable divs.
11. **Freshness:** "published" and "updated" dates on the page; reviewed every 30–90 days.

Source: Rob Hoffman (contact.so), Nov 2026, on how Meta's assistant reads sites. The literature note is in the
space's knowledge base.

## What these skills may never do
- Send, post, pitch or email anything. They draft, check and rank; the owner's gates still apply.
- Create an account, take a trial or spend credits (Medialyst, DataForSEO, Moz, Google APIs).
- Deploy a site change, or submit to IndexNow or Search Console.
- Name a client or claim a pilot. Claim sheets override any skill's suggested copy.

## Where outputs go (content, never in this module)
- `[space]/1-tracks/comms/reports/YYYY-MM-DD-<topic>-report.md`: audits, scans, panel reports.
- `[space]/1-tracks/comms/newsjack/`: the monitor profile, `brief.md` and run artifacts.
- `[space]/1-tracks/comms/coverage-graph/<date>/`: graph data and its local HTML view.
- `[space]/1-tracks/comms/geo/`: the AI-visibility panel (versioned).
