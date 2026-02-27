# Campaign Dashboard (/today hook)

## Hook Context

This hook is called by `/today` to inject a campaign section into the daily briefing. It reads state from `.datacore/state/campaign-state.json` (populated by `campaign-monitor.py`).

## Instructions

When generating the /today briefing, include a **Campaign** section. Read the state file and generate the section below.

### Step 1: Read State

Read `.datacore/state/campaign-state.json`. If file is missing or stale (>24h), run the monitor first:
```bash
python3 .datacore/modules/comms/lib/campaign-monitor.py
```

### Step 2: Generate Section

Generate this section in the briefing:

```markdown
### Campaign (@FairDataSociety)

**Followers**: [current] ([+/-growth] this week)
[Include sparkline-style trend if history available: 782 → 782 → 783 → 782 → ...]

**Today's Post**: [time UTC] — "[first 60 chars of content]..."
[Or "No post scheduled today" if none]

**Yesterday's Performance**:
[Show the most recently published post's analytics]
- [content preview]: [impressions] impressions, [likes] likes, [shares] shares, [engagement_rate]% engagement
[Or "No posts published yet" if analytics empty]

**Replies Needing Response**: [total comment_count across all reply posts]
[List each post with comments, showing comment_count and link to reply on X]
- "[post_content preview]" — [comment_count] comments ([permalink])
[If zero posts with comments: "All caught up."]

**This Week's Queue**: [N posts scheduled]
[List next 3 scheduled posts with date and content preview]

**Top Performer** (all time):
[Post with highest engagement rate — content preview and metrics]
```

### Step 3: Flag Actions

If there are posts with comments (comment_count > 0), add to the **Needs Your Decision** section:
```markdown
- [ ] Check [N] comments on @FairDataSociety posts (reply on X)
```

If no posts are scheduled for the next 3 days, flag:
```markdown
- [ ] Generate content — no posts scheduled after [date]
```

## State File Format

```json
{
  "last_updated": "2026-02-17T...",
  "analytics": [
    {
      "id": "...",
      "content": "first 80 chars...",
      "published_at": "...",
      "permalink": "https://twitter.com/i/web/status/...",
      "impressions": 18,
      "likes": 0,
      "comments": 0,
      "shares": 0,
      "clicks": 0,
      "engagement_rate": 0
    }
  ],
  "followers": {
    "current": 782,
    "growth": 0,
    "history": [{"date": "2026-02-10", "followers": 782}, ...]
  },
  "replies": [
    {
      "post_id": "...",
      "post_content": "first 60 chars...",
      "comment_count": 5,
      "permalink": "https://twitter.com/i/web/status/...",
      "like_count": 1
    }
  ],
  "published": [
    {"id": "...", "content": "first 80 chars...", "published_at": "...", "has_media": true}
  ],
  "scheduled": [
    {"id": "...", "content": "first 80 chars...", "scheduled_for": "...", "status": "scheduled", "has_media": true}
  ]
}
```

## Campaign Context

Active campaign: **Product Public Testing** (Feb 16 - Apr 12, 2026)
Campaign plan: `0-personal/1-active/projects/my-product/comms/campaigns/product-launch/campaign-plan-v3.md`
Performance insights: `0-personal/1-active/projects/my-product/comms/campaigns/product-launch/metrics/performance-insights.md`
