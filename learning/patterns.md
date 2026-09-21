# Comms Module - Learned Patterns

Patterns discovered through campaign planning sessions. Reference when creating new campaigns.

---

## Session: Product Launch Campaign (2026-01-01)

### Pattern: Privacy-Preserving Growth Hacking

**Context**: Traditional referral programs track users, which contradicts privacy product messaging.

**Solution**: ZK-based anonymous rewards
- Users generate cryptographic commitment (hash of secret)
- Commitment added to Merkle tree (no identity link)
- Claim rewards via ZK proof ("I know a secret in this tree")
- No tracking of who referred whom

**Implementation Insight**: Use the product itself for claims (dogfooding)
- Drop ZK proof into product's own Honest Inbox
- "We literally can't see who claimed what"
- No separate claim infrastructure needed

**Marketing Value**: "The first referral program that doesn't track you" - unique, newsworthy

---

### Pattern: AI-Driven Execution Model

**Context**: Human bandwidth is the bottleneck for consistent content.

**Solution**: AI generates, human reviews
```
QUARTERLY → Human approves strategy (1 hour)
MONTHLY → Human reviews calendar (30 min)
DAILY → Human quick-approves content queue (15 min)
```

**Key Insight**: Human touchpoints should be approval gates, not creation work.

---

### Pattern: Meta/Dogfooding Campaign Tactics

**Context**: Best way to prove product works is to use it in marketing.

**Examples from MyProduct**:
- "Someone sent us our own roadmap. We can't read it."
- "Bug report received. No idea who sent it. Fixed anyway."
- Use product's Honest Inbox for ZK reward claims

**Why It Works**:
- Proves functionality
- Creates memorable content
- Demonstrates confidence in product

---

### Pattern: Attract Sneezers, Don't Chase

**Context**: Traditional sneezer outreach requires weeks of human relationship building.

**Solution**: Create content sneezers want to share organically
1. Make content remarkable (Purple Cow test)
2. Give sneezers social currency for sharing
3. Tag/mention strategically in relevant discussions
4. Let quality spread naturally

**Automated Tactics**:
- Monitor hashtags, reply with value
- Quote-tweet news with product angle
- Create shareable assets (comparisons, infographics)

**Human-Required (Minimal)**:
- Weekly: Review AI-suggested mentions (10 min)
- Monthly: 2-3 personalized DMs to Tier 1 targets

---

### Pattern: X Ads Test → Learn → Scale

**Context**: Budget is limited, need efficient spend.

**Approach**:
| Phase | Budget | Goal |
|-------|--------|------|
| Testing (Wk 1-4) | $50/week | Test audiences, creatives |
| Optimize (Wk 5-8) | $75/week | Double down on winners |
| Scale (Wk 9-12) | $100-150/week | Scale winning combinations |

**Rules**:
- Kill underperformers after 48 hours
- Only boost content with >3% organic engagement
- Weekly review cycle

---

### Pattern: Guerilla Marketing for Privacy Products

**High-Impact Stunts**:
1. **Privacy Policy Shame Tool** - Interactive landing page comparing data collection
2. **"Leak Test" Challenge** - Bounty for anyone who can extract user data
3. **"We Can't Read This" Campaign** - Meta humor about product's privacy

**Tactical Guerilla**:
- Reply-hijack data breach news
- Quote-tweet roast competitor privacy claims
- Privacy policy teardowns (line-by-line analysis)

---

### Pattern: Web3 Community Partnerships

**Key Partner Identified**: [Web3Privacy Now](https://web3privacy.info/)
- 3,000+ event visitors, 70 countries
- Speakers: Vitalik, Chelsea Manning, Roger Dingledine
- Perfect alignment for privacy products

**Touchpoints**:
- Submit to their privacy tools database
- Guest post on Week in Privacy News
- Sponsor hackathon tracks
- Offer product for whistleblower use cases at events

---

### Pattern: Kaito AI / Yapping for Crypto Visibility

**Context**: [Kaito AI](https://www.kaito.ai/) scores crypto Twitter content, rewards quality "yapping".

**How to Leverage**:
- Tag @KaitoAI in privacy/crypto threads
- Build "smart follower" graph
- Consistent posting on privacy topics
- Coordinate high-quality threads with launch timing

---

### Pattern: AI Video for Tutorials

**Recommended Tool**: [HeyGen](https://www.heygen.com/) ($29-89/mo)
- Best quality, 4K, 40+ languages
- Custom avatar capability

**Video Types**:
| Type | Length | Frequency |
|------|--------|-----------|
| Product tutorial | 60 sec | Once |
| Feature explainer | 60 sec | Per feature |
| Educational series | 45-90 sec | Weekly |

**Advanced**: Create custom AI persona avatar for brand consistency

---

### Pattern: Privacy Product Positioning Differentiators

When positioning privacy products, lead with:

1. **Zero-leak architecture** - "Not even metadata escapes"
2. **Honest Inbox** (if applicable) - Anonymous inbound messaging
3. **Agent-native** - API-first for AI/automation use cases
4. **Cypherpunk heritage** - Connect to movement history
5. **Open source** - "Verify, don't trust"

**Identity Transformation** (Apple/Tesla effect):
- Make users feel proud, not paranoid
- "I opted out of surveillance"
- "I'm part of the solution"

---

## Session: Campaign Restructure (2026-01-11)

### Pattern: Hub-and-Spoke Campaign Architecture

**Context**: Launching multiple related products (MyProduct + PartnerDrive).

**Decision**: Single account (@YourOrganization) with interleaved content, NOT separate accounts.

**Why**:
- Separate accounts fragment audience
- Users don't want to follow multiple accounts
- Content categories within same account provide variety
- "Scroll-through coherence" - any scroll shows coherent brand

**Structure**:
```
@YourOrganization (Hub)
├── MyProduct content (spoke)
├── PartnerDrive content (spoke)
├── Movement content (spoke)
└── Teaser content (spoke)
```

---

### Pattern: Re-emergence Narrative Structure

**Context**: Organization was dormant since 2018, needs compelling return story.

**Structure**:
1. **Hook**: "Not dead. Building."
2. **Past**: What was built (infrastructure, specs, learnings)
3. **Present**: "Now: products"
4. **Future**: What's coming (teasers)

**Teaser Arc**:
- Day 1: "We've been quiet. Not dead. Building."
- Day 2: "Since 2018: infrastructure, specs, learnings. Now: products."
- Day 3: "Jan 19. Welcome to the next stage."

---

### Pattern: Counter-Culture vs Enterprise Voice Split

**Context**: Organization is counter-culture (cypherpunks), but needs enterprise reach (Davos).

**Solution**: Separate voice channels

| Account | Voice | Audience | Davos? |
|---------|-------|----------|--------|
| @YourOrganization | Counter-culture | Cypherpunks, privacy advocates | No |
| @YourEnterprise | Enterprise | Investors, institutions | Yes |
| @Personal | Bridge | Both worlds | Yes |

**Key Insight**: Counter-culture brand SHOULD NOT speak at establishment venues. It dilutes credibility with core audience.

---

### Pattern: Technical Feature as Economic Primitive

**Context**: Data escrow is "just a feature" vs "defining primitive for fair data exchange".

**Framing Shift**:
- ❌ "We have pay-to-access content"
- ✅ "Data escrow: the primitive that makes fair data exchange real"

**Why It Works**:
- Elevates from feature to foundation
- Creates narrative arc (privacy → economy)
- Positions for ecosystem, not just product

---

### Pattern: Feature Request Bot for Engagement

**Context**: Need engagement that's useful, not just noise.

**Execution**:
```
@YourOrganization myproduct: document preview please
→ Bot: "📝 Logged! Track it: github.com/org/myproduct/issues/123"
```

**Creates**:
- Public roadmap transparency
- Community ownership of direction
- Engagement that's actually useful
- GitHub Issues as single source of truth
- Optional: token rewards for quality requests

---

## Strategic References

- [[Ecosystem-Architecture]] - Token strategy, agent DAOs, fractal ecosystems
- Campaign: `0-personal/1-active/projects/my-product/comms/campaigns/product-launch/campaign-plan-v2.md`

---

---

## Session: X Compliance Architecture (2026-06-04)

### Pattern: X Portfolio Enforcement Cascade

**Context**: @FDS account was suspended during session. @plur_ai subsequently received a "verify yourself" lock even though it had not been involved in any violation.

**Pattern**: X enforcement can propagate across accounts that share ownership signals (email, phone, IP, payment method). When one account in a portfolio faces enforcement, check all sibling accounts immediately.

**Action**: After any X suspension or lock — log into each portfolio account via web, check for pending verification challenges, and resolve before automation resumes.

**Tags**: #x #enforcement #portfolio #suspension #cascade #compliance

---

### Pattern: X 403 "Temporarily Locked" — Try Web First

**Context**: API 403 errors with "temporarily locked" messaging appeared after @FDS suspension. Initial read was "permanent enforcement".

**Pattern**: A 403 "temporarily locked" via the API is not necessarily permanent. X uses this code for verify-yourself challenges that are clearable via a web browser login. Web login presents the challenge; completing it restores API access.

**Rule**: Before concluding an account is permanently locked: log in via web browser, check for and complete any challenge, then retry API calls.

**Tags**: #x #403 #locked #verify #web-login #api #enforcement

---

### Pattern: Compliant AI-Assisted Social Posting Architecture (Pattern B+D)

**Context**: Research into compliant X automation frameworks (2026-06-04 deep research session) converged on a single industry pattern.

**Pattern**: The strongest ToS-compliant AI posting architecture physically separates agent capability from the publish action:

```
Agent pipeline:
  draft-writer → filesystem drafts
  (agent stops here — cannot post)

Human pipeline:
  human reviews drafts → approves → runs posting CLI
  (CLI is human-only — not callable by agent)
```

This is "Pattern B+D": filesystem drafts + human-only posting CLI.

**The draft→review→publish boundary**: Draft and Approved states are agent-writable. Publish is a human gate. The publish CLI should require explicit human invocation — not a scheduled job, not an API endpoint the agent can hit.

**Why this matters**: X ToS prohibits automated posting that bypasses human review for promotional/engagement content. Pattern B+D provides a defensible audit trail: every post has a human approval event before publication.

**Tags**: #x #compliance #architecture #draft #review #publish #human-gate #pattern-bd

---

### Pattern: OSS Tool README Reliability — Verify Before Trusting

**Context**: Research session evaluated several open-source X/social automation tools claiming ToS compliance.

**Finding**: README highlights routinely overstate implementation maturity:
- **noslop**: Claimed agent-safe posting; actual repo was stale, agent could still invoke post endpoint directly
- **agent-content-pipeline**: Claimed X auth support; implementation used browser cookie extraction (banned by X ToS)

**Rule**: Before adopting any OSS tool for X automation:
1. Check last commit date — stale = unmaintained = claims may not reflect code
2. Read the actual auth implementation, not just the README auth section
3. Verify the human-gate is structural (agent literally cannot invoke it), not just documented

**Tags**: #oss #readme #trust #x #auth #compliance #due-diligence

---

### Pattern: Retiring Engagement Scripts as Valid Strategic Choice

**Context**: User authorized retiring all automated engagement scripts (likes, follows, replies) during the X compliance review session.

**Pattern**: Engagement automation that was built for growth is a liability during enforcement scrutiny. When accounts are under review, retiring engagement scripts entirely is preferable to auditing them under time pressure.

**User preference**: "Engagement scripts need to be retired for now anyway." Reddit scanning code was preserved as a useful primitive shape (value: audience research, not engagement automation).

**Tags**: #x #engagement #automation #compliance #retire #strategic

---

## Corrections Log

*No corrections logged yet. Add human feedback here.*

---

*Last updated: 2026-06-04*
