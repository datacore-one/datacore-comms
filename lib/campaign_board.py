#!/usr/bin/env python3
"""Campaign board: validate a campaign.yaml and render it as one HTML page.

The campaign counterpart of .datacore/lib/roadmap_render.py, and laid out the
same way: the plan first (the stories, why now, the phases, how a piece moves),
then the board in columns, then what could go wrong and what it costs people.
campaign.yaml is the single writable source; this page is generated from it.
It reuses the roadmap board's CSS so the two read as one system.

The validator refuses what an operator could not act on: a deliverable with no
definition of done, a public piece with no human approver or gate, an unknown
person, track, gate or source, a date that does not parse, effort booked to an
agent or to an item that does not exist, and out-of-range risk or confidence
values. Weekdays are computed from ISO dates, never typed into the YAML.

Usage:
    python3 campaign_board.py validate <campaign.yaml>
    python3 campaign_board.py html <campaign.yaml> [--out campaign.html]
"""
import argparse
import datetime as dt
import html
import re
import sys
from pathlib import Path

import yaml

STATUSES = {"planned", "in_progress", "done", "blocked", "unverified", "optional", "proposed"}
SOURCES = {"fellowship", "owner", "design", "playbook", "briefing"}
EVIDENCE = {"url", "artifact", "metric", "decision", "merged-pr", "screenshot", "test", "signed"}
LEVELS = {"low", "medium", "high"}
REQUIRED_TOP = ("campaign", "people", "tracks", "gates", "deliverables")


def _date(s):
    try:
        return dt.date.fromisoformat(str(s))
    except ValueError:
        return None


def validate(c: dict) -> list:
    errs = []
    for k in REQUIRED_TOP:
        if k not in c:
            errs.append(f"missing top-level key: {k}")
    if errs:
        return errs
    people, tracks, gates = c["people"], c["tracks"], c["gates"]
    human = {k for k, p in people.items() if p.get("kind") == "human"}
    for gid, g in gates.items():
        if g.get("who") and g["who"] not in people:
            errs.append(f"gate {gid}: unknown person {g['who']}")
    seen = set()
    for d in c["deliverables"]:
        did = d.get("id", "?")
        if did in seen:
            errs.append(f"{did}: duplicate id")
        seen.add(did)
        for f in ("track", "type", "title", "status", "source", "due"):
            if not d.get(f):
                errs.append(f"{did}: missing {f}")
        if d.get("track") and d["track"] not in tracks:
            errs.append(f"{did}: unknown track {d['track']}")
        if d.get("status") and d["status"] not in STATUSES:
            errs.append(f"{did}: unknown status {d['status']}")
        if d.get("source") and d["source"] not in SOURCES:
            errs.append(f"{did}: unknown source {d['source']}")
        for role in ("drafter", "owner"):
            if d.get(role) and d[role] not in people:
                errs.append(f"{did}: unknown {role} {d[role]}")
        due = d.get("due")
        if due and due != "continuous" and not _date(due):
            errs.append(f"{did}: bad date {due}")
        for k in ("start", "end"):
            if d.get(k) and not _date(d[k]):
                errs.append(f"{did}: bad {k} date {d[k]}")
        if d.get("public"):
            ap = d.get("approver")
            if not ap:
                errs.append(f"{did}: public piece needs an approver")
            elif ap not in people:
                errs.append(f"{did}: unknown approver {ap}")
            elif ap not in human:
                errs.append(f"{did}: approver {ap} is not a human")
            if not d.get("gate"):
                errs.append(f"{did}: public piece needs a gate")
        if d.get("gate") and d["gate"] not in gates:
            errs.append(f"{did}: unknown gate {d['gate']}")
        dw = d.get("done_when")
        if not dw:
            errs.append(f"{did}: missing done_when")
        else:
            for f in ("condition", "evidence", "verify"):
                if not dw.get(f):
                    errs.append(f"{did}: done_when needs {f}")
            if dw.get("evidence") and dw["evidence"] not in EVIDENCE:
                errs.append(f"{did}: unknown evidence {dw['evidence']}")
    for ev in c.get("events", []):
        if not _date(ev.get("date")):
            errs.append(f"event {ev.get('title')}: bad date {ev.get('date')}")
        if ev.get("end") and not _date(ev["end"]):
            errs.append(f"event {ev.get('title')}: bad end {ev['end']}")
    for ph in c.get("phases", []):
        if not (_date(ph.get("start")) and _date(ph.get("end"))):
            errs.append(f"phase {ph.get('id')}: bad dates")
    for st in c.get("streams", []):
        if not (_date(st.get("start")) and _date(st.get("end"))):
            errs.append(f"stream {st.get('id')}: bad dates")
        for i in st.get("ids", []):
            if i not in {d.get("id") for d in c["deliverables"]}:
                errs.append(f"stream {st.get('id')}: unknown item {i}")
    for iid, per in (c.get("human_effort") or {}).items():
        if iid not in seen:
            errs.append(f"human_effort: unknown item {iid}")
        for who in per:
            if who not in human:
                errs.append(f"human_effort {iid}: {who} is not a human")
    for r in c.get("recurring_human", []):
        if r.get("person") not in human:
            errs.append(f"recurring_human: {r.get('person')} is not a human")
        if not (_date(r.get("start")) and _date(r.get("end"))):
            errs.append(f"recurring_human {r.get('what')}: bad dates")
    for r in c.get("risks", []):
        for f in ("likelihood", "impact"):
            if r.get(f) not in LEVELS:
                errs.append(f"risk {r.get('id')}: {f} {r.get(f)} not in low/medium/high")
        if r.get("owner") and r["owner"] not in people:
            errs.append(f"risk {r.get('id')}: unknown owner {r['owner']}")
    for i in c.get("infra", []):
        v = i.get("confidence")
        if not isinstance(v, (int, float)) or not 0 <= v <= 100:
            errs.append(f"infra {i.get('part')}: confidence {v} not 0–100")
    ids = seen | {x["id"] for x in c.get("decisions", [])}
    for cp in c.get("critical_paths", []):
        for s in cp.get("steps", []):
            if re.fullmatch(r"[A-Z]+-\w+", str(s.get("ref", ""))) and s["ref"] not in ids:
                errs.append(f"critical path {cp.get('name')}: unknown ref {s['ref']}")
            if not _date(s.get("date")):
                errs.append(f"critical path {cp.get('name')}: bad date {s.get('date')}")
    return errs


# ---------------------------------------------------------------- helpers

def e(s):
    return html.escape("" if s is None else str(s))


def fmt(d):
    x = _date(d)
    return f"{x:%a} {x.day} {x:%b}" if x else e(d)


def _css():
    """The roadmap board's CSS, so both boards read as one system."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
        import roadmap_render
        return roadmap_render.CSS
    except Exception:
        return "body{font-family:system-ui;margin:2rem;max-width:1100px}"


def _phase_of(d, phases):
    if d["due"] == "continuous":
        return "all"
    x = _date(d["due"])
    for ph in phases:
        if _date(ph["start"]) <= x <= _date(ph["end"]):
            return ph["id"]
    return phases[-1]["id"] if phases else "all"


def _overlap_weeks(a0, a1, b0, b1):
    lo, hi = max(a0, b0), min(a1, b1)
    return ((hi - lo).days + 1) / 7 if hi >= lo else 0


def human_hours(c):
    """{person: {phase_id: hours}} from one-off effort plus recurring loads."""
    phases = c.get("phases", [])
    by_id = {d["id"]: d for d in c["deliverables"]}
    out = {}
    for iid, per in (c.get("human_effort") or {}).items():
        ph = _phase_of(by_id[iid], phases) if iid in by_id else "all"
        for who, h in per.items():
            out.setdefault(who, {}).setdefault(ph, 0)
            out[who][ph] += h
    for r in c.get("recurring_human", []):
        r0, r1 = _date(r["start"]), _date(r["end"])
        for ph in phases:
            w = _overlap_weeks(r0, r1, _date(ph["start"]), _date(ph["end"]))
            if w:
                out.setdefault(r["person"], {}).setdefault(ph["id"], 0)
                out[r["person"]][ph["id"]] += w * r["hours_per_week"]
    return out


def _h(x):
    return f"{x:.1f}".rstrip("0").rstrip(".") if x else "—"


EXTRA_CSS = """
[hidden]{display:none!important}

.gantt{border:1px solid var(--rule);border-radius:12px;background:var(--surface);overflow:hidden}
.gr{display:grid;grid-template-columns:230px minmax(260px,1fr) 300px;gap:16px;padding:14px 16px;border-top:1px solid var(--rule-soft);align-items:center}
.gr.gh{border-top:0;padding-bottom:4px}
.gl b{display:block;font:600 14.5px Outfit,system-ui;color:var(--ink)}
.gl small{font:11.5px 'JetBrains Mono',monospace;color:var(--muted)}
.gl{display:grid;grid-template-columns:26px 1fr;column-gap:10px;align-items:start}
.gl .gn{grid-row:span 2}
.gn{width:26px;height:26px;border-radius:50%;background:var(--raised);color:var(--ink);font:600 13px/26px Outfit,system-ui;text-align:center}
.gr:nth-child(2) .gn{background:var(--gated);color:#fff}
.gt2{position:relative;height:22px;background:repeating-linear-gradient(90deg,var(--raised) 0 1px,transparent 1px 100%);border-radius:4px}
.gt2.ax{height:16px;background:none}
.tk{position:absolute;top:0;font:11px 'JetBrains Mono',monospace;color:var(--faint)}
.bar{position:absolute;top:4px;height:14px;border-radius:7px;background:var(--accent);opacity:.75}
.bar.bb{background:var(--gated);opacity:1}
.evm{position:absolute;top:-2px;bottom:-2px;width:2px;background:var(--gated-soft)}
.nowm{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--halt)}
.gx p{margin:0 0 4px;font-size:12.5px;color:var(--body)}
.gx .k{display:block;font:500 10px 'JetBrains Mono',monospace;text-transform:uppercase;letter-spacing:.1em;color:var(--faint)}
.gx .k.thr{color:var(--human)}
.gt tr.grp td{font:600 11.5px Outfit,system-ui;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);background:var(--raised)}
.mut{color:var(--muted);font-size:13px}
.wfs{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin-top:16px}
.wf{border:1px solid var(--rule);border-radius:10px;background:var(--surface);padding:14px 16px}
.wf h3{margin:0 0 8px;font:600 14.5px Outfit,system-ui;color:var(--ink)}
.wf ol{margin:0;padding-left:20px;font-size:13.5px;color:var(--body)}
.wf li{margin:3px 0}
@media (max-width:980px){.gr{grid-template-columns:1fr}}
.lede b{color:var(--ink)}
.steps{border:1px solid var(--rule);border-radius:12px;background:var(--surface);overflow:hidden}
.st{display:grid;grid-template-columns:200px 1fr;gap:24px;padding:24px;border-top:1px solid var(--rule-soft)}
.st:first-child{border-top:0}
.st.proof{background:var(--accent-soft)}
.st .nm{font:400 24px/1.2 Literata,Georgia,serif;color:var(--accent)}
.st .sh{font-size:13px;color:var(--muted);margin-top:4px}
.st .mo{display:inline-block;margin-bottom:10px;font:500 10.5px 'JetBrains Mono',monospace;text-transform:uppercase;
  letter-spacing:.12em;color:var(--accent);background:var(--accent-soft);padding:3px 8px;border-radius:4px}
.st .pb{color:var(--muted);font-size:14.5px;margin:0 0 10px}
.st .hl{font:400 21px/1.3 Literata,Georgia,serif;color:var(--ink);margin:0 0 8px}
.st .tx{font-size:14.5px;color:var(--body);margin:0 0 8px}
.st .wh{font-size:13px;color:var(--muted);margin:0 0 12px}
.st .rule{padding:10px 14px;border-radius:8px;background:var(--raised);font-size:13.5px;color:var(--accent)}
.st .rule b{display:block;font:500 10px 'JetBrains Mono',monospace;letter-spacing:.12em;color:var(--faint);margin-bottom:4px}
.why li{margin:6px 0;color:var(--body)}
.tl{display:flex;border:1px solid var(--rule);border-radius:10px;overflow:hidden;margin:0 0 6px}
.tl div{padding:10px 12px;border-left:1px solid var(--rule);background:var(--surface);min-width:0}
.tl div:first-child{border-left:0}
.tl div.now{background:var(--accent-soft)}
.tl b{display:block;font:600 13.5px Outfit,system-ui;color:var(--ink)}
.tl span{font:11.5px 'JetBrains Mono',monospace;color:var(--muted)}
.tlev{display:flex;flex-wrap:wrap;gap:6px 18px;margin:8px 0 22px;font-size:13px;color:var(--gated)}
.ph{display:grid;grid-template-columns:150px 1fr;gap:22px;padding:20px 0;border-top:1px solid var(--rule)}
.ph .pid{font:12px 'JetBrains Mono',monospace;color:var(--faint)}
.ph .pn{font:400 22px/1.2 Literata,Georgia,serif;color:var(--ink)}
.ph .pd{font:12px 'JetBrains Mono',monospace;color:var(--muted);margin-top:4px}
.ph .pb{font-size:15px;color:var(--ink);margin:0 0 8px}
.ph .pg{font-size:13px;color:var(--muted);margin:0 0 10px}
.ph .pg b{color:var(--gated);font-weight:500;margin-right:6px}
.ph ul{margin:0;padding-left:18px;font-size:14px;color:var(--body)}
.ph li{margin:3px 0}
.ph li a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--rule)}
.ph .hrs{margin-top:10px;font-size:13px;color:var(--muted)}
.flow{overflow-x:auto;border:1px solid var(--rule);border-radius:12px;background:var(--paper);padding:12px}
.flow svg{display:block;width:100%;min-width:760px;height:auto}
.dg-lane{fill:var(--surface)} .dg-lane.alt{fill:var(--raised)}
.dg-lanelabel{font:500 8px 'Geist Mono',monospace;letter-spacing:.14em;fill:var(--muted)}
.dg-line{fill:none;stroke:var(--muted);stroke-width:1.2}
.dg-line.dash{stroke-dasharray:5 4}
.dg-line.accent{stroke:var(--accent);stroke-width:1.6}
.dg-mk{fill:var(--muted)} .dg-mk-accent{fill:var(--accent)}
.dg-mask{fill:var(--surface)} .dg-mask.alt{fill:var(--raised)} .dg-mask.top{fill:var(--paper)}
.dg-al{font:500 8px 'Geist Mono',monospace;letter-spacing:.06em;fill:var(--muted);text-anchor:middle}
.dg-al.accent{fill:var(--accent)}
.dg-node rect{fill:var(--surface);stroke:var(--ink);stroke-width:1}
.dg-node.input rect{fill:var(--raised);stroke:var(--faint)}
.dg-node.store rect{fill:var(--raised);stroke:var(--muted)}
.dg-node.focal rect{fill:var(--accent-soft);stroke:var(--accent);stroke-width:1.4}
.dg-name{font:600 12px Geist,Outfit,system-ui;fill:var(--ink);text-anchor:middle}
.dg-sub{font:400 8px 'Geist Mono',monospace;fill:var(--muted);text-anchor:middle}
.dg-rule{stroke:var(--rule);stroke-width:.8}
.dg-lg{fill:var(--surface);stroke:var(--ink);stroke-width:1} .dg-lg.focal{fill:var(--accent-soft);stroke:var(--accent)}
.dg-lgt{font:400 8px 'Geist Mono',monospace;fill:var(--muted)}
.fbar{position:sticky;top:0;z-index:5;display:grid;gap:8px;padding:12px 14px;margin:0 0 18px;
  background:var(--paper);border:1px solid var(--rule);border-radius:12px}
.frow{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.frow .fl{font:500 10.5px 'JetBrains Mono',monospace;text-transform:uppercase;letter-spacing:.1em;color:var(--faint);min-width:64px}
.fbar input[type=search]{flex:1 1 220px;font:14px Outfit,system-ui;padding:7px 10px;border:1px solid var(--rule);
  border-radius:8px;background:var(--surface);color:var(--ink)}
.fbar .cnt{margin-left:auto;font:12.5px 'JetBrains Mono',monospace;color:var(--muted)}
.fbar .rst{font:13px Outfit,system-ui;padding:6px 12px;border-radius:8px;border:1px solid var(--rule);background:transparent;color:var(--body);cursor:pointer}
.bcols{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:18px;align-items:start}
.bcol>h3{margin:0 0 2px;font:400 21px Literata,Georgia,serif;color:var(--ink)}
.bcol>h3 small{font:12px 'JetBrains Mono',monospace;color:var(--faint);margin-left:6px}
.bcol>.sub{font-size:12.5px;color:var(--muted);margin:0 0 6px;padding-bottom:10px;border-bottom:2px solid var(--ink)}
.bcol.fel>.sub{border-color:var(--gated)}
.pgrp{margin:14px 0 6px;font:500 10.5px 'JetBrains Mono',monospace;text-transform:uppercase;letter-spacing:.12em;color:var(--faint)}
.dv{display:block;padding:12px 13px;margin:0 0 8px;border:1px solid var(--rule);border-left:3px solid var(--rule);
  border-radius:8px;background:var(--surface);scroll-margin-top:160px}
.dv.fel{border-left-color:var(--gated)}
.dv:target{box-shadow:0 0 0 3px var(--accent-soft);border-color:var(--accent)}
.dv-top{display:flex;gap:8px;align-items:baseline}
.dv-id{font:11px 'JetBrains Mono',monospace;color:var(--faint);white-space:nowrap}
.dv-title{margin:0;font:600 14.5px/1.35 Outfit,system-ui;color:var(--ink)}
.dv-when{margin:6px 0 4px;font-size:12.5px;color:var(--ink)}
.dv-when b{font-weight:600}
.dv-when .who{color:var(--muted)}
.dv-out{margin:0 0 8px;font-size:13px;line-height:1.45;color:var(--muted)}
.dv-tags{display:flex;flex-wrap:wrap;gap:4px}
.pill{font:500 11px Outfit,system-ui;padding:2px 7px;border-radius:999px;background:var(--raised);color:var(--body)}
.pill.fel{background:var(--gated-soft);color:var(--gated)}
.pill.s-unverified,.pill.s-proposed{background:var(--human-soft);color:var(--human)}
.pill.s-optional{background:transparent;border:1px dashed var(--rule);color:var(--faint)}
.pill.s-in_progress,.pill.s-done{background:var(--go-soft);color:var(--go)}
.pill.s-blocked{background:var(--halt-soft);color:var(--halt)}
.dv-needs{margin:8px 0 0;padding:6px 9px;border-radius:6px;background:var(--human-soft);color:var(--ink);font-size:12.5px}
.dv-needs b{color:var(--human);margin-right:4px}
.dv-done{margin-top:8px;font-size:12.5px;color:var(--body)}
.dv-done summary{cursor:pointer;color:var(--faint)}
.dv-done p{margin:4px 0 0}
.empty{padding:24px;text-align:center;color:var(--muted)}
.cp{margin:0 0 22px}
.cp h3{margin:0 0 4px;font:600 15px Outfit,system-ui;color:var(--ink)}
.cp p{margin:0 0 10px;font-size:13.5px;color:var(--muted)}
.chain{display:flex;flex-wrap:wrap;align-items:stretch}
.lk{position:relative;padding:8px 12px;margin:0 22px 8px 0;border:1px solid var(--rule);border-radius:8px;background:var(--surface);font-size:13px;max-width:200px}
.lk:not(:last-child)::after{content:"→";position:absolute;right:-18px;top:50%;transform:translateY(-50%);color:var(--muted)}
.lk .d{display:block;font:11.5px 'JetBrains Mono',monospace;color:var(--accent)}
.lk .r{font:11px 'JetBrains Mono',monospace;color:var(--faint)}
.gt{width:100%;border-collapse:collapse;font-size:14px}
.gt th,.gt td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--rule-soft);vertical-align:top}
.gt th{font:600 11.5px Outfit,system-ui;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.gt td.id{font:12px 'JetBrains Mono',monospace;color:var(--faint);white-space:nowrap}
.gt .n{text-align:right;font-variant-numeric:tabular-nums}
.gt tr.tot td{font-weight:600;border-top:2px solid var(--rule)}
.lv{font:500 11px Outfit,system-ui;padding:2px 8px;border-radius:999px}
.lv.high{background:var(--halt-soft);color:var(--halt)} .lv.medium{background:var(--human-soft);color:var(--human)}
.lv.low{background:var(--go-soft);color:var(--go)}
.conf{display:flex;align-items:center;gap:8px;white-space:nowrap}
.conf .bar-track{flex:0 0 90px;height:6px;border-radius:3px;background:var(--raised);overflow:hidden;margin:0}
.conf .bar-fill{height:100%;background:var(--go)}
.conf.lo .bar-fill{background:var(--halt)} .conf.mid .bar-fill{background:var(--human)}
.big{display:flex;gap:18px;align-items:center;padding:16px 18px;border:1px solid var(--rule);border-radius:12px;background:var(--surface);margin:0 0 14px}
.big .n{font:600 34px Outfit,system-ui;color:var(--human)}
.big p{margin:0;font-size:14.5px;color:var(--body)}
.dec{display:grid;gap:10px}
.dec .card b{display:block;margin-bottom:4px;color:var(--ink)}
.dec .rec{color:var(--go);font-size:13.5px}
.cal{display:grid;gap:8px;margin-top:12px}
.wk{display:grid;grid-template-columns:140px 1fr;gap:14px;padding:10px 14px;border:1px solid var(--rule);border-radius:10px;background:var(--surface)}
.wk.now{border-color:var(--accent)}
.wk .wd b{display:block;color:var(--ink);font:600 13.5px Outfit,system-ui}
.wk ul{margin:0;padding:0;list-style:none;display:grid;gap:4px}
.wk li{font-size:13.5px;color:var(--body)}
.wk li .d{display:inline-block;font:11.5px 'JetBrains Mono',monospace;color:var(--faint);min-width:78px}
.wk li a{color:var(--ink);text-decoration:none;border-bottom:1px solid var(--rule)}
.wk .ev-row{color:var(--gated)}
details.more>summary{cursor:pointer;color:var(--accent);font-size:14px}
@media (max-width:1100px){.bcols{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:680px){.bcols{grid-template-columns:1fr}.st,.ph,.wk{grid-template-columns:1fr}.tl{flex-direction:column}}
"""

FILTER_JS = """
(function(){
  const bar=document.getElementById('fbar'); if(!bar) return;
  const cards=[...document.querySelectorAll('.dv')];
  const q=bar.querySelector('input[type=search]'), cnt=bar.querySelector('.cnt');
  const chips=[...bar.querySelectorAll('.chip')];
  const empty=document.getElementById('empty');
  function active(){ const a={}; chips.forEach(c=>{ if(c.getAttribute('aria-pressed')==='true') a[c.dataset.g]=c.dataset.v; }); return a; }
  function apply(){
    const t=q.value.trim().toLowerCase(), a=active(); let n=0;
    cards.forEach(r=>{
      let ok=!t||r.dataset.text.includes(t);
      for(const g in a){ if(ok&&!(' '+r.dataset[g]+' ').includes(' '+a[g]+' ')) ok=false; }
      r.hidden=!ok; if(ok) n++;
    });
    document.querySelectorAll('.pgrp').forEach(h=>{
      let s=h.nextElementSibling, any=false;
      while(s&&s.classList.contains('dv')){ if(!s.hidden) any=true; s=s.nextElementSibling; }
      h.hidden=!any;
    });
    cnt.textContent=n+' of '+cards.length+' shown'; empty.hidden=!!n;
  }
  chips.forEach(c=>c.addEventListener('click',()=>{
    const on=c.getAttribute('aria-pressed')==='true';
    chips.filter(x=>x.dataset.g===c.dataset.g).forEach(x=>x.setAttribute('aria-pressed','false'));
    c.setAttribute('aria-pressed',on?'false':'true'); apply();
  }));
  q.addEventListener('input',apply);
  bar.querySelector('.rst').addEventListener('click',()=>{q.value='';chips.forEach(x=>x.setAttribute('aria-pressed','false'));apply();});
  apply();
})();
"""


def _column_of(d, cols):
    for i, col in enumerate(cols):
        if col.get("source"):
            if d.get("source") == col["source"]:
                return i
        elif d["track"] in col.get("tracks", []):
            return i
    return len(cols) - 1


def stream_of(d, streams):
    """First stream that claims the item: explicit ids, then source, then track."""
    for st in streams:
        if d["id"] in st.get("ids", []):
            return st
    for st in streams:
        if st.get("source") and d.get("source") == st["source"]:
            return st
    for st in streams:
        if not st.get("source") and d["track"] in st.get("tracks", []):
            return st
    return None


def render_streams(c, today):
    streams, ds = c.get("streams", []), c["deliverables"]
    w = c["campaign"].get("window", {})
    a, b = _date(w.get("start")), _date(w.get("end"))
    if not (streams and a and b):
        return ""
    span = (b - a).days + 1
    pct = lambda x: max(0, min(100, ((x - a).days) / span * 100))
    ticks = ""
    m = dt.date(a.year, a.month, 1)
    while m <= b:
        if m >= a:
            ticks += f'<span class="tk" style="left:{pct(m):.2f}%">{m:%b}</span>'
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    marks = "".join(f'<span class="evm" style="left:{pct(_date(ev["date"])):.2f}%" title="{e(ev["title"])}"></span>'
                    for ev in c.get("events", []))
    now = f'<span class="nowm" style="left:{pct(today):.2f}%"></span>' if a <= today <= b else ""
    rows = ""
    for i, st in enumerate(streams, 1):
        s0, s1 = _date(st["start"]), _date(st["end"])
        n = sum(1 for d in ds if stream_of(d, streams) is st)
        cls = "bar bb" if i == 1 else "bar"
        rows += (f'<div class="gr"><div class="gl"><span class="gn">{i}</span><b>{e(st["name"])}</b>'
                 f'<small>{n} deliverables · from {fmt(st["start"])}</small></div>'
                 f'<div class="gt2">{marks}{now}<span class="{cls}" style="left:{pct(s0):.2f}%;'
                 f'width:{pct(s1) - pct(s0) + 100 / span:.2f}%"></span></div>'
                 f'<div class="gx"><p>{e(st.get("role", ""))}</p>'
                 + (f'<p><span class="k">starts when</span>{e(st["expands_when"])}</p>' if st.get("expands_when") else "")
                 + f'<p><span class="k thr">if we are the bottleneck</span>{e(st.get("throttle", ""))}</p></div></div>')
    return f'<div class="gantt"><div class="gr gh"><div></div><div class="gt2 ax">{ticks}</div><div></div></div>{rows}</div>'


def render_cadences(c):
    cads, wfs = c.get("cadences", []), c.get("workflows", [])
    if not (cads or wfs):
        return ""
    people = c["people"]
    name = lambda k: people.get(k, {}).get("name", k)
    order = ["daily", "weekly", "monthly", "per piece"]
    rows = ""
    for every in order + sorted({x["every"] for x in cads} - set(order)):
        mine = [x for x in cads if x["every"] == every]
        if not mine:
            continue
        rows += f'<tr class="grp"><td colspan="5">{e(every.capitalize())}</td></tr>'
        for x in mine:
            rows += (f'<tr><td><b>{e(x["what"])}</b>' + (f'<br><span class="mut">{e(x["when"])}</span>' if x.get("when") else "")
                     + f'</td><td>{e(name(x.get("who", "")))}</td><td>{e(x.get("output", ""))}</td>'
                     f'<td>{e(x.get("gate", ""))}</td><td>{e(x.get("runs", ""))}'
                     + (' <span class="pill s-proposed">new</span>' if x.get("new") else "") + '</td></tr>')
    wf = "".join(f'<div class="wf"><h3>{e(w["name"])}</h3><ol>'
                 + "".join(f"<li>{e(s)}</li>" for s in w["steps"]) + "</ol>"
                 + (f'<p class="mut">{e(w["note"])}</p>' if w.get("note") else "") + "</div>" for w in wfs)
    return (f'<table class="gt"><tr><th>What</th><th>Who</th><th>Produces</th><th>Gate</th><th>Runs as</th></tr>{rows}</table>'
            f'<div class="wfs">{wf}</div>')


def card(d, c, phases):
    people, gates = c["people"], c["gates"]
    name = lambda k: people.get(k, {}).get("name", k) if k else ""
    fel = d.get("source") == "fellowship"
    lead = d.get("drafter") or d.get("owner")
    ap = d.get("approver")
    when = "Ongoing" if d["due"] == "continuous" else fmt(d["due"])
    if d.get("start"):
        when = f'{fmt(d["start"])} → {when}'
    flow = e(name(lead)) if lead else ""
    if ap:
        flow += f' → <b>{e(name(ap))}</b>' if flow else f'<b>{e(name(ap))}</b> approves'
    status = {"optional": "bonus", "unverified": "status not checked"}.get(d["status"], d["status"].replace("_", " "))
    tags = f'<span class="pill">{e(d["type"])}</span>'
    if d["status"] != "planned":
        tags += f'<span class="pill s-{e(d["status"])}">{e(status)}</span>'
    if fel:
        tags += '<span class="pill fel">Fellowship Plan</span>'
    if d.get("public"):
        tags += f'<span class="pill">public · {e(d.get("gate", ""))}</span>'
    needs = f'<p class="dv-needs"><b>Needs</b>{e(d["needs"])}</p>' if d.get("needs") else ""
    dw = d["done_when"]
    gate = gates.get(d.get("gate"), {}).get("title", "")
    done = (f'<details class="dv-done"><summary>Done when</summary><p>{e(dw["condition"])}</p>'
            f'<p>Check: {e(dw["verify"])} <span class="dv-id">({e(dw["evidence"])})</span></p>'
            f'{f"<p>Gate: {e(gate)}</p>" if gate else ""}</details>')
    who = " ".join(sorted({k for k in (lead, ap, d.get("owner"), d.get("drafter")) if k}))
    kind = " ".join(x for x, on in (("fellowship", fel), ("public", d.get("public")), ("needs", d.get("needs")),
                                    ("checkme", d["status"] in ("unverified", "proposed", "optional"))) if on)
    text = " ".join(str(v) for v in (d["id"], d["title"], d.get("outcome", ""), d.get("channel", ""),
                                     name(lead), name(ap), d.get("needs", ""), d["type"])).lower()
    data = {"phase": _phase_of(d, phases), "who": who, "kind": kind, "type": d["type"], "text": text}
    attrs = " ".join(f'data-{k}="{e(v)}"' for k, v in data.items())
    return (f'<article class="dv{" fel" if fel else ""}" id="{e(d["id"])}" {attrs}>'
            f'<div class="dv-top"><span class="dv-id">{e(d["id"])}</span><h4 class="dv-title">{e(d["title"])}</h4></div>'
            f'<p class="dv-when"><b>{when}</b> <span class="who">· {flow}</span></p>'
            f'<p class="dv-out">{e(d.get("outcome", ""))}</p>'
            f'<div class="dv-tags">{tags}</div>{needs}{done}</article>')


def _filters(c, ds, phases):
    people = c["people"]

    def chips(g, label, opts):
        b = "".join(f'<button type="button" class="chip" data-g="{g}" data-v="{e(v)}" aria-pressed="false">{e(t)}</button>'
                    for v, t in opts)
        return f'<div class="frow"><span class="fl">{label}</span>{b}</div>'

    used = sorted({p for d in ds for p in (d.get("drafter"), d.get("owner"), d.get("approver")) if p},
                  key=lambda p: (people[p].get("kind") != "human", p))
    ph = [(p["id"], p["name"]) for p in phases] + [("all", "Ongoing")]
    counts = {}
    for d in ds:
        counts[d["type"]] = counts.get(d["type"], 0) + 1
    types = [(t, f"{t} {n}") for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    return ('<div class="fbar" id="fbar"><div class="frow">'
            '<input type="search" placeholder="Search titles, people, channels…" aria-label="Search deliverables">'
            '<button type="button" class="rst">Reset</button><span class="cnt"></span></div>'
            + chips("type", "Type", types)
            + chips("phase", "Phase", ph)
            + chips("who", "Person", [(p, people[p]["name"]) for p in used])
            + chips("kind", "Show", [("fellowship", "Fellowship Plan"), ("public", "Public pieces"),
                                    ("needs", "Has a precondition"), ("checkme", "Not confirmed")])
            + "</div>")


# ---------------------------------------------------------------- page

def render_html(c: dict, today: dt.date | None = None, base: Path | None = None) -> str:
    today = today or dt.date.today()
    meta, ds, people = c["campaign"], c["deliverables"], c["people"]
    name = lambda k: people.get(k, {}).get("name", k)
    phases = c.get("phases", [])
    errs = validate(c)
    w = meta.get("window", {})
    start, end = _date(w.get("start")), _date(w.get("end"))
    by_id = {d["id"]: d for d in ds}

    hh = human_hours(c)
    tot_h = lambda who: sum(hh.get(who, {}).values())
    n_pub = sum(1 for d in ds if d.get("public"))
    n_fel = sum(1 for d in ds if d.get("source") == "fellowship")
    n_dec = sum(1 for x in c.get("decisions", []) if not x.get("decided"))
    metrics = [(len(ds), "deliverables"), (n_fel, "from the<br>Fellowship Plan"),
               (n_pub, "public pieces,<br>each approved by a person"),
               (f'{_h(tot_h("gregor"))} h', "of Gregor's time<br>this quarter (est.)"),
               (n_dec, "open decisions", "alarm")]
    mhtml = "".join(f'<div class="metric {m[2] if len(m) > 2 else ""}"><span class="n">{m[0]}</span>'
                    f'<span class="l">{m[1]}</span></div>' for m in metrics)

    steps = "".join(
        f'<div class="st{" proof" if n.get("proof") else ""}"><div><div class="nm">{e(n["title"])}</div>'
        f'<div class="sh">{e(n.get("short", ""))}</div></div><div>'
        f'<span class="mo">{e(n.get("motive", ""))}</span>'
        f'<p class="pb">{e(n.get("problem", ""))}</p><p class="hl">{e(n.get("headline", ""))}</p>'
        f'<p class="tx">{e(n.get("text", ""))}</p><p class="wh">{e(n.get("who", ""))}</p>'
        f'<div class="rule"><b>CLAIM RULE</b>{e(n.get("rule", ""))}</div></div></div>'
        for n in c.get("narratives", []))
    why = "".join(f"<li>{e(x)}</li>" for x in c.get("why_now", []))

    tl = "".join(f'<div class="{"now" if _date(p["start"]) <= today <= _date(p["end"]) else ""}" '
                 f'style="flex:{(_date(p["end"]) - _date(p["start"])).days + 1}"><b>{e(p["name"])}</b>'
                 f'<span>{fmt(p["start"])} – {fmt(p["end"])}</span></div>' for p in phases)
    tlev = "".join(f'<span>◆ {fmt(ev["date"])} {e(ev["title"])}</span>' for ev in c.get("events", []))
    phs = ""
    for p in phases:
        mine = sorted((d for d in ds if _phase_of(d, phases) == p["id"]), key=lambda d: str(d["due"]))
        key = [d for d in mine if d.get("source") == "fellowship" or d.get("public")][:8]
        li = "".join(f'<li><span class="dv-id">{fmt(d["due"])}</span> <a href="#{e(d["id"])}">{e(d["title"])}</a></li>'
                     for d in key)
        more = len(mine) - len(key)
        hrs = " · ".join(f'{e(name(who))} {_h(v[p["id"]])} h'
                         for who, v in sorted(hh.items(), key=lambda kv: -kv[1].get(p["id"], 0)) if v.get(p["id"]))
        phs += (f'<div class="ph"><div><div class="pid">{e(p["id"])}</div><div class="pn">{e(p["name"])}</div>'
                f'<div class="pd">{fmt(p["start"])} – {fmt(p["end"])}</div></div><div>'
                f'<p class="pb">{e(p.get("becomes", ""))}</p>'
                f'<p class="pg"><b>cannot start until</b>{e(p.get("gate", ""))}</p>'
                f'<ul>{li}</ul>' + (f'<p class="hrs">+ {more} more on the board</p>' if more > 0 else "")
                + f'<p class="hrs">People this phase (est.): {hrs or "—"}</p></div></div>')

    flow = ""
    if meta.get("diagram") and base and (base / meta["diagram"]).exists():
        flow = (f'<section><div class="sec-head"><h2>How a piece moves</h2><span class="eyebrow">same for every deliverable</span></div>'
                f'<p class="sec-sub">Agents prepare, check and route. A person decides before anything is published, '
                f'and results feed next month’s plan.</p><figure class="flow">{(base / meta["diagram"]).read_text()}</figure></section>')

    cols = c.get("columns") or [{"name": t["name"], "tracks": [k]} for k, t in c["tracks"].items()]
    buckets = [[] for _ in cols]
    for d in ds:
        buckets[_column_of(d, cols)].append(d)
    order = {p["id"]: i for i, p in enumerate(phases)} | {"all": len(phases)}
    pname = {p["id"]: p["name"] for p in phases} | {"all": "Ongoing"}
    board = ""
    for col, items in zip(cols, buckets):
        items.sort(key=lambda d: (order[_phase_of(d, phases)], str(d["due"])))
        body, cur = "", None
        for d in items:
            ph = _phase_of(d, phases)
            if ph != cur:
                body += f'<div class="pgrp">{e(pname[ph])}</div>'
                cur = ph
            body += card(d, c, phases)
        cls = "bcol fel" if col.get("source") == "fellowship" else "bcol"
        board += (f'<div class="{cls}"><h3>{e(col["name"])}<small>{len(items)}</small></h3>'
                  f'<p class="sub">{e(col.get("about", ""))}</p>{body}</div>')

    cps = ""
    for cp in c.get("critical_paths", []):
        lk = ""
        for s in cp["steps"]:
            ref = s["ref"]
            tag = f'<a href="#{e(ref)}" class="r">{e(ref)}</a>' if ref in by_id else f'<span class="r">{e(ref)}</span>'
            lk += f'<div class="lk"><span class="d">{fmt(s["date"])}</span>{e(s["note"])} {tag}</div>'
        cps += f'<div class="cp"><h3>{e(cp["name"])}</h3><p>{e(cp.get("why", ""))}</p><div class="chain">{lk}</div></div>'

    rank = {"high": 0, "medium": 1, "low": 2}
    rrows = "".join(
        f'<tr><td class="id">{e(r["id"])}</td><td><b>{e(r["risk"])}</b><br><span style="color:var(--muted)">'
        f'Early sign: {e(r.get("early_warning", ""))}</span></td>'
        f'<td><span class="lv {e(r["likelihood"])}">{e(r["likelihood"])}</span></td>'
        f'<td><span class="lv {e(r["impact"])}">{e(r["impact"])}</span></td>'
        f'<td>{e(r.get("mitigation", ""))}</td><td>{e(name(r.get("owner", "")))}</td></tr>'
        for r in sorted(c.get("risks", []), key=lambda r: (rank[r["impact"]], rank[r["likelihood"]])))

    weeks = max(1, ((end - start).days + 1) / 7) if start and end else 1
    hrows, totals = "", [0.0] * len(phases)
    for who in sorted(hh, key=lambda k: -tot_h(k)):
        vals = [hh[who].get(p["id"], 0) for p in phases]
        totals = [a + b for a, b in zip(totals, vals)]
        q = tot_h(who)
        hrows += (f'<tr><td><b>{e(name(who))}</b></td>' + "".join(f'<td class="n">{_h(v)}</td>' for v in vals)
                  + f'<td class="n"><b>{_h(q)}</b></td><td class="n">{_h(q / weeks)}</td></tr>')
    hrows += ('<tr class="tot"><td>All people</td>' + "".join(f'<td class="n">{_h(t)}</td>' for t in totals)
              + f'<td class="n">{_h(sum(tot_h(k) for k in hh))}</td><td></td></tr>')
    hhead = "".join(f'<th class="n">{e(p["name"])}</th>' for p in phases)
    rec = "".join(f'<li>{e(name(r["person"]))}: {e(r["what"])} — {_h(r["hours_per_week"])} h a week</li>'
                  for r in c.get("recurring_human", []))

    inf = c.get("infra", [])
    avg = round(sum(i["confidence"] for i in inf) / len(inf)) if inf else 0
    irows = ""
    for i in sorted(inf, key=lambda i: -i["confidence"]):
        v = i["confidence"]
        lvl = "lo" if v < 50 else "mid" if v < 70 else ""
        irows += (f'<tr><td><b>{e(i["part"])}</b><br><span style="color:var(--muted)">{e(i.get("used_for", ""))}</span></td>'
                  f'<td><div class="conf {lvl}"><div class="bar-track"><div class="bar-fill" style="width:{v}%"></div></div>{v}%</div></td>'
                  f'<td>{e(i.get("evidence", ""))}</td><td>{e(i.get("fallback", ""))}</td></tr>')

    grows = "".join(f'<tr><td class="id">{e(gid)}</td><td><b>{e(g["title"])}</b><br>'
                    f'<span style="color:var(--muted)">{e(g.get("what", ""))}</span></td>'
                    f'<td>{e(name(g.get("who", "")))}</td><td>{e(g.get("when", ""))}</td>'
                    f'<td class="n">{sum(1 for d in ds if d.get("gate") == gid)}</td></tr>'
                    for gid, g in c["gates"].items())
    prows = "".join(f'<tr><td><b>{e(p["name"])}</b></td><td>{e(p.get("kind"))}</td><td>{e(p.get("role", ""))}</td></tr>'
                    for p in people.values())
    decs = "".join(f'<div class="card"><b>{e(x["id"])} · {e(x["question"])}</b>'
                   f'<span class="rec">Recommended: {e(x.get("recommended", ""))}</span></div>'
                   for x in c.get("decisions", []) if not x.get("decided"))
    kpis = "".join(f'<tr><td><b>{e(k["kpi"])}</b></td><td>{e(k["target"])}</td><td>{e(k.get("source", ""))}</td></tr>'
                   for k in c.get("kpis", []))
    cal = ""
    if start and end:
        s0 = start - dt.timedelta(days=start.weekday())
        while s0 <= end:
            s1 = s0 + dt.timedelta(days=6)
            rows = []
            for ev in c.get("events", []):
                a, b = _date(ev["date"]), _date(ev.get("end") or ev["date"])
                if a <= s1 and b >= s0:
                    rows.append((a, f'<li class="ev-row"><span class="d">{fmt(ev["date"])}</span>◆ {e(ev["title"])}</li>'))
            for d in ds:
                x = _date(d["due"]) if d["due"] != "continuous" else None
                if x and s0 <= x <= s1:
                    rows.append((x, f'<li><span class="d">{fmt(d["due"])}</span><a href="#{e(d["id"])}">{e(d["title"])}</a></li>'))
            rows.sort(key=lambda r: r[0])
            cal += (f'<div class="wk{" now" if s0 <= today <= s1 else ""}"><div class="wd"><b>Week of {fmt(s0.isoformat())}</b></div>'
                    f'<ul>{"".join(r[1] for r in rows) or "<li>—</li>"}</ul></div>')
            s0 += dt.timedelta(days=7)

    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(meta["title"])}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Literata:ital,wght@0,300;0,400;1,300;1,400&family=JetBrains+Mono:wght@300;400&family=Geist:wght@400;600&family=Geist+Mono:wght@400;500&display=swap">
<style>{_css()}{EXTRA_CSS}</style>
<header><div class="head-in">
  <div class="head-top">
    <div style="display:flex;flex-direction:column;gap:10px">
      <span class="eyebrow">Campaign board · updated {e(meta.get("updated", ""))}</span>
      <h1>{e(meta["title"])}</h1>
    </div>
    <span class="valid">{len(ds)} deliverables · {len(errs)} errors</span>
  </div>
  <div class="srcline"><span>generated from <b>{e(meta.get("source_file", "campaign.yaml"))}</b></span>
    <span>·</span><span>{fmt(w.get("start"))} → {fmt(w.get("end"))}</span></div>
  <p class="lede"><b>{e(meta.get("question", ""))}</b> {e(meta.get("summary", ""))}</p>
</div></header>
<div class="wrap">
  <div class="metrics">{mhtml}</div>
  <section><div class="sec-head"><h2>The campaign in four stories</h2><span class="eyebrow">one idea</span></div>
    <p class="sec-sub">Every piece tells one of these stories on its own. Together they say
    <b>{e(meta.get("line", ""))}</b> and <b>{e(meta.get("proof", ""))}</b></p>
    <div class="steps">{steps}</div></section>
  <section><div class="sec-head"><h2>Why now</h2></div><ul class="why">{why}</ul></section>
  {f'<section><div class="sec-head"><h2>Build order</h2><span class="eyebrow">backbone first</span></div><p class="sec-sub">{e(c.get("streams_note", ""))}</p>{render_streams(c, today)}</section>' if c.get("streams") else ""}
  <section><div class="sec-head"><h2>The quarter in five phases</h2><span class="eyebrow">what each makes true</span></div>
    <div class="tl">{tl}</div><div class="tlev">{tlev}</div>{phs}</section>
  {flow}
  {f'<section><div class="sec-head"><h2>Cadences and workflows</h2><span class="eyebrow">how the work runs</span></div><p class="sec-sub">{e(c.get("cadences_note", ""))}</p>{render_cadences(c)}</section>' if (c.get("cadences") or c.get("workflows")) else ""}
  <section><div class="sec-head"><h2>The board</h2><span class="eyebrow">every deliverable</span></div>
    <p class="sec-sub">Four columns, each in phase order. A purple edge marks the Fellowship Plan, whose dates
    were shared with Swarm Foundation. Click a chip to filter; click it again to clear.</p>
    {_filters(c, ds, phases)}
    <div class="bcols">{board}</div>
    <p class="empty" id="empty" hidden>Nothing matches these filters.</p></section>
  <section><div class="sec-head"><h2>Critical path</h2><span class="eyebrow">what everything else waits on</span></div>{cps}</section>
  <section><div class="sec-head"><h2>Risks</h2><span class="eyebrow">highest impact first</span></div>
    <table class="gt"><tr><th></th><th>Risk</th><th>Likelihood</th><th>Impact</th><th>What we do</th><th>Owner</th></tr>{rrows}</table></section>
  <section><div class="sec-head"><h2>Human time</h2><span class="eyebrow">estimate, hours</span></div>
    <p class="sec-sub">{e(c.get("effort_notes", ""))}</p>
    <table class="gt"><tr><th>Person</th>{hhead}<th class="n">Quarter</th><th class="n">Per week</th></tr>{hrows}</table>
    <p class="sec-sub" style="margin-top:14px">Counted every week:</p><ul class="why">{rec}</ul></section>
  <section><div class="sec-head"><h2>How far we can rely on the infrastructure</h2><span class="eyebrow">confidence</span></div>
    <div class="big"><span class="n">{avg}%</span><p>{e(c.get("infra_summary", ""))}</p></div>
    <table class="gt"><tr><th>Part</th><th>Confidence</th><th>Evidence</th><th>If it fails</th></tr>{irows}</table></section>
  <section><div class="sec-head"><h2>Open decisions</h2></div><div class="dec">{decs}</div></section>
  <section><div class="sec-head"><h2>Where a person decides</h2></div>
    <table class="gt"><tr><th></th><th>Gate</th><th>Who</th><th>When</th><th class="n">Items</th></tr>{grows}</table></section>
  <section><div class="sec-head"><h2>Who does what</h2></div>
    <table class="gt"><tr><th>Who</th><th>Kind</th><th>Campaign job</th></tr>{prows}</table></section>
  <section><div class="sec-head"><h2>Measures</h2></div>
    <table class="gt"><tr><th>Measure</th><th>Target</th><th>From</th></tr>{kpis}</table></section>
  <section><div class="sec-head"><h2>Week by week</h2></div>
    <details class="more"><summary>Show the full calendar</summary><div class="cal">{cal}</div></details></section>
  <footer><span>generated by .datacore/modules/comms/lib/campaign_board.py</span>
    <span>edit campaign.yaml, then regenerate; do not edit this page</span></footer>
</div>
<script>{FILTER_JS}</script>"""


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["validate", "html"])
    ap.add_argument("file")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    src = Path(a.file)
    c = yaml.safe_load(src.read_text())
    errs = validate(c)
    for x in errs:
        print(f"ERROR {x}", file=sys.stderr)
    if a.cmd == "validate":
        print(f"{len(c.get('deliverables', []))} deliverables, {len(errs)} errors")
        return 1 if errs else 0
    if errs:
        return 1
    out = Path(a.out) if a.out else src.with_suffix(".html")
    out.write_text(render_html(c, base=src.resolve().parent))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
