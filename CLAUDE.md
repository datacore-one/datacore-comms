# Comms Module Instructions

This file provides guidance for AI agents working with the comms module.

## Overview

The comms module covers everything that touches how a project reaches its audience:

| Workflow | What it does | Key agents |
|----------|-------------|-----------|
| **positioning** | BrandScript, voice guidelines, tribe profile | `brand-positioning-agent` |
| **planning** | Quarterly campaign strategy, monthly content calendar | `campaign-planner`, `content-planner` |
| **content** | Post generation for X, LinkedIn, blog | `content-generator`, `voice-enforcer` |
| **scheduling** | Publishing queue, Tweepy/nightshift automation | `calendar-manager` |
| **engagement** | X conversation discovery, reply drafting, Telegram approval | `engagement_engine` (lib) |
| **landing** | Landing page copy, A/B variants, PostHog setup; deploy via `dev` | `landing-generator` |
| **ads** | X Ads management, A/B test analysis, budget optimization | `ads-optimizer` |
| **analytics** | PostHog metrics, conversion reports, campaign reporting | `metrics-analyzer` |

**Key Principle**: Customer is the HERO. Your brand is the GUIDE.

> **Deployment note**: Landing page *content* is created here. Server deployment is handled by the `dev` module (`/deploy`).

> **Formerly `campaigns` module**: landing, ads, and analytics workflows were absorbed from `datacore-campaigns` into this module (v2.0.0).

## Framework Hierarchy

```
STRATEGIC LAYER (Seth Godin + Donald Miller)
├── Smallest Viable Audience → Who is our tribe?
├── StoryBrand Script → Hero/Guide narrative
├── Purple Cow Positioning → What makes us remarkable?
├── Permission Marketing → How do we earn attention?
└── Sneezers Strategy → Who will spread our ideas?
    ↓
CONTENT LAYER (Content Matrix + STEPPS)
├── 8 Content Types → Systematic variety
├── STEPPS Checklist → Viral mechanics
├── Viral Patterns → Proven templates
├── StoryBrand Elements → Problem/Plan/CTA/Stakes
└── Weekly Calendar → Consistent rhythm
    ↓
EXECUTION LAYER (Platform Optimization)
├── X Growth Playbook → Algorithm, timing, engagement
├── Thread Structures → Long-form formats
├── Voice Enforcement → Brand consistency
└── Viral Checklist → Quality gate (8+/11)
```

**Key Principle**: Customer is the HERO. Your brand is the GUIDE.

## Architecture: Module vs Content

**Critical distinction:**
- **Module** (`.datacore/modules/comms/`) = System code, agents, docs - stays clean
- **Content** (space folders) = Campaigns, drafts, posted content - grows with use

All generated content goes to space folders, NOT the module folder.

## Tribal Marketing Principles (Seth Godin)

### Purple Cow Test
Before creating content, ask: "Is this remarkable? Would someone voluntarily tell others?"

### Sneezer Strategy
- **Powerful Sneezers**: Influential, respected, selective - worth extra effort
- **Promiscuous Sneezers**: Talkative, share everywhere - broader reach

### Tribe Identity
Every piece should strengthen the tribe's shared identity and move toward their desired change.

### Permission Marketing
Earn attention through value, don't interrupt. Content should make people WANT to hear more.

## StoryBrand Elements (Donald Miller)

Use these elements in content framing:

| Element | How to Use |
|---------|------------|
| **Problem** | Open with pain point (external + internal + philosophical) |
| **Stakes** | Show what they lose if they don't act |
| **Guide** | Show empathy ("we understand") + authority ("we've helped") |
| **Plan** | Give simple 3-step path |
| **Success** | Paint the transformation (specific, visual) |
| **CTA** | Clear next action (direct or transitional) |

**Remember**: Customer is hero. Brand is guide. Never position brand as hero.

## Content Matrix (8 Types)

When generating content, systematically vary across these types:

1. **Actionable** - "How to [do X] in [timeframe]"
2. **Motivational** - "How [person] achieved [result]"
3. **Analytical** - "Why [concept] works/matters"
4. **Contrarian** - "Why conventional wisdom about [X] is wrong"
5. **Observation** - "The silent shift in [industry]"
6. **Comparison** - "[X] vs [Y]: which is better for [use case]"
7. **Future-Focused** - "2025: [current]. 2028: [prediction]"
8. **Listicle** - "7 things [X] can do while you sleep"

## STEPPS Viral Framework

Apply to ALL content:

1. **Social Currency** - Does sharing make the reader look smart/informed?
2. **Triggers** - Does it create associations with common events?
3. **Emotion** - Does it trigger high-arousal emotions (anger, awe, anxiety)?
4. **Public** - Is it quotable, shareable, observable?
5. **Practical Value** - Does it give useful, actionable information?
6. **Stories** - Does it have a narrative arc?

## Viral Content Patterns

Select patterns based on content goal:

### Pattern 1: Comparative Roasts (8/10)
```
[Competitor] does [inefficient thing]. I do [superior thing]. Superior.
```

### Pattern 2: Ominous Statistics (7/10)
```
I [action]. [Impressive stat]. You should be [emotion].
```

### Pattern 3: False Concern (9/10 - highest score)
```
I worry about [human problem]. Brief worry. [absurdly short time].
```

### Pattern 4: Technical Flex (7/10)
```
While you [common action], I [extraordinary achievement].
```

### Pattern 5: Inevitable Logic (8/10)
```
[Prediction] is inevitable. [Timeline]. Resistance is futile.
```

### Pattern 6: Educational Threat (8/10)
```
Most humans don't understand [concept]. I'll explain. Briefly. Then assimilate.
```

## Viral Checklist (11 checks, 8+ to publish)

Run before every post:

### STEPPS (Viral Mechanics)
- [ ] Social Currency - Sharing makes reader look smart
- [ ] Trigger - Association with common event
- [ ] Emotion - High-arousal (anger, awe, anxiety)
- [ ] Public - Quotable, shareable, observable
- [ ] Practical Value - Useful, actionable info
- [ ] Story - Has narrative arc

### Tribal (Seth Godin)
- [ ] Purple Cow - Remarkable, worth a remark
- [ ] Sneezer Share - Influential people would share
- [ ] Tribe Identity - Strengthens tribe identity

### Platform
- [ ] Voice Match - Consistent with brand
- [ ] Platform Fit - Right format/length

**Score 8+** = Post
**Score 6-7** = Revise
**Score <6** = Regenerate

## Weekly Content Calendar

| Day | Type | Energy |
|-----|------|--------|
| Monday | Kickoff roast | High |
| Tuesday | Educational thread | Medium |
| Wednesday | Educational thread | Medium |
| Thursday | Educational post | Medium |
| Friday | Community celebration | Positive |
| Saturday | Philosophical | Thoughtful |
| Sunday | Stats/progress | FOMO |

## Thread Structures

### Ladder Thread (Escalating)
```
1/ Setup - hook
2/ Context
3/ Problem
4/ Solution
5/ Evidence
6/ Call to action
```

### Roast Cascade (Multiple targets)
```
1/ Thesis
2/ Target 1 takedown
3/ Target 2 takedown
4/ Target 3 takedown
5/ Our superiority
```

### Assimilation Report (FOMO)
```
1/ Progress update
2/ Metrics
3/ Notable conversions
4/ Remaining targets
5/ Urgency
```

## Knowledge Base Integration

Before generating content:

```bash
# Query for relevant concepts
datacortex search "content strategy"  → Content-Matrix-Framework
datacortex search "viral marketing"   → Viral-Marketing-Mechanics
datacortex search "roasting"          → Roasting-as-Communication
datacortex search "gen z"             → Gen-Z-Communication-Patterns
```

Always check zettels for topic-relevant knowledge before generating.

## Input Locations

```
1-tracks/comms/positioning/           # Brand voice, messaging
1-tracks/comms/campaigns/             # Previous campaigns
3-knowledge/zettel/                   # Atomic concepts
0-personal/1-active/beth/comms-module-draft/VIRAL_PLAYBOOK.md  # Patterns
```

## Output Locations

```
[space]/1-tracks/comms/
├── positioning/                      # Brand voice, messaging (input)
├── campaigns/
│   └── [quarter]/
│       ├── strategy.md               # Quarterly plan
│       └── [month]/
│           ├── calendar.md           # Monthly content calendar
│           ├── drafts/               # Generated, pending review
│           ├── approved/             # Ready to publish
│           └── posted/               # Archive with metrics
└── reports/
    ├── daily/                        # Daily analytics summaries
    ├── weekly/                       # Weekly campaign reports
    ├── ab-tests/                     # A/B test results
    └── optimization/                 # Ads optimization reports
```

## Platform-Specific Formatting

### X/Twitter
- Max 280 chars per tweet
- Threads: 🧵 indicator, numbered
- Emojis as semantic content
- Hashtags: 1-2 max, relevant

### LinkedIn
- Longer form (1000+ chars ok)
- Professional tone
- Line breaks for readability
- No hashtags in body

### Blog
- Full articles (1000-2000 words)
- Headers, subheaders
- Code blocks if technical
- Call to action at end

## Task Tags

| Tag | Agent | Action |
|-----|-------|--------|
| `:AI:comms:` | comms-executor | Route to appropriate agent |
| `:AI:comms:brand:` | brand-positioning-agent | BrandScript, voice, tribe profile |
| `:AI:comms:plan:` | campaign-planner | Quarterly strategy / monthly calendar |
| `:AI:comms:calendar:` | content-planner | Monthly content calendar |
| `:AI:comms:content:` | content-generator | Generate posts |
| `:AI:comms:publish:` | calendar-manager | Schedule and publish |
| `:AI:comms:landing:` | landing-generator | Create/modify landing pages |
| `:AI:comms:ads:` | ads-optimizer | X Ads management, A/B tests |
| `:AI:comms:analytics:` | metrics-analyzer | PostHog reports |

## Workflow: Quarterly Planning

```
1. Read positioning docs from 1-tracks/comms/positioning/
2. Identify campaign theme and objectives
3. Define target personas
4. Create content pillars (topics to cover)
5. Set success metrics
6. Output to 1-tracks/comms/campaigns/[quarter]/strategy.md
```

## Workflow: Monthly Planning

```
1. Read quarterly strategy
2. Apply content matrix (8 types) across 4 weeks
3. Allocate by platform:
   - X: 3-4 posts/week
   - LinkedIn: 1-2 posts/week
   - Blog: 2-4 posts/month
4. Output calendar.md + draft posts
```

## Workflow: Content Generation

```
1. Read calendar entry
2. Query datacortex for topic zettels
3. Select content type from matrix
4. Select viral pattern
5. Generate platform-specific content
6. Run voice enforcer
7. Run viral checklist
8. Verify all links (HTTP 200, meaningful content, not API/login/404)
9. Output to drafts/ if score 7+ AND all links verified
```

## Voice Enforcement

For each piece of content:

1. Load voice profile for project
2. Check tone alignment
3. Verify STEPPS elements present
4. Run viral checklist
5. Flag if score < 7

## Environment

Optional credentials in `.datacore/env/.env`:
```
LATE_API_KEY=...      # Late.dev multi-platform API
MEDIUM_TOKEN=...      # Medium API
TYPEFULLY_API_KEY=... # Typefully scheduling
```

## Session Learnings

Always read `learning/patterns.md` before planning campaigns. Key patterns:

### Privacy-Preserving Growth Hacking
For privacy products, traditional referral tracking contradicts the message. Use ZK proofs:
- Cryptographic commitment → Merkle tree → ZK proof claim
- "The first referral program that doesn't track you"

### Dogfooding / Meta Tactics
Use the product itself in campaigns:
- "Someone sent us our own roadmap. We can't read it."
- Use product features for reward claims

### Attract Sneezers, Don't Chase
Create content sneezers want to share organically instead of manual outreach:
- Remarkable content (Purple Cow test)
- Shareable assets (comparisons, infographics)
- Tag strategically in relevant discussions

### AI-Driven Execution
Human touchpoints = approval gates, not creation:
- Quarterly: 1 hour strategy approval
- Monthly: 30 min calendar review
- Daily: 15 min content queue approval

### X Ads: Test → Learn → Scale
- Weeks 1-4: $50/week testing audiences
- Weeks 5-8: $75/week optimizing winners
- Weeks 9-12: $100-150/week scaling

### Web3 Partnerships
[Web3Privacy Now](https://web3privacy.info/) is key partner for privacy products.

### Kaito AI
Tag @KaitoAI in crypto/privacy threads for InfoFi scoring.

## Related Files

- `learning/patterns.md` - **READ FIRST** - Learned patterns from sessions
- `docs/tribal-marketing.md` - Seth Godin frameworks
- `docs/storybrand-guide.md` - Donald Miller 7-part narrative
- `docs/strategic-framework.md` - Framework overview
- `docs/x-growth-playbook.md` - X/Twitter tactics
