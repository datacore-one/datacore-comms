"""campaign_board: one campaign.yaml is the source; the validator refuses what an
operator could not act on, and the HTML view is generated from it."""
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))


def _campaign(**over):
    c = {
        "campaign": {"id": "t", "title": "T", "window": {"start": "2026-10-01", "end": "2026-12-18"}},
        "people": {"alex": {"name": "Alex", "kind": "human"},
                   "data": {"name": "Data", "kind": "agent"}},
        "tracks": {"acme": {"name": "Acme"}},
        "gates": {"H2": {"title": "Every public piece", "who": "alex"}},
        "deliverables": [{
            "id": "D-001", "track": "acme", "type": "blog", "title": "A post",
            "public": True, "drafter": "data", "approver": "alex", "gate": "H2",
            "due": "2026-10-15", "status": "planned", "source": "fellowship",
            "done_when": {"condition": "Published", "evidence": "url", "verify": "open the URL"},
        }],
    }
    c.update(over)
    return c


def test_valid_campaign_passes():
    from campaign_board import validate
    assert validate(_campaign()) == []


def test_public_piece_without_approver_is_refused():
    from campaign_board import validate
    c = _campaign()
    del c["deliverables"][0]["approver"]
    assert any("approver" in e for e in validate(c))


def test_missing_done_when_is_refused():
    from campaign_board import validate
    c = _campaign()
    del c["deliverables"][0]["done_when"]
    assert any("done_when" in e for e in validate(c))


def test_unknown_person_gate_track_and_bad_date_are_refused():
    from campaign_board import validate
    c = _campaign()
    d = c["deliverables"][0]
    d.update(drafter="nobody", gate="H99", track="nope", due="2026-13-40")
    errs = " ".join(validate(c))
    for word in ("nobody", "H99", "nope", "2026-13-40"):
        assert word in errs


def test_agent_cannot_be_approver():
    from campaign_board import validate
    c = _campaign()
    c["deliverables"][0]["approver"] = "data"
    assert any("approver" in e for e in validate(c))


def test_html_shows_deliverable_and_weekday_computed(tmp_path):
    from campaign_board import render_html
    out = render_html(_campaign())
    assert "A post" in out and "D-001" in out
    assert "Thu 15 Oct" in out  # weekday derived from the date, never typed


def test_real_campaign_file_validates():
    from campaign_board import validate
    import os
    p = Path(os.environ.get("COMMS_CAMPAIGN_YAML", "/nonexistent/campaign.yaml"))
    if not p.exists():
        import pytest
        pytest.skip("campaign.yaml lives in the user's space")
    assert validate(yaml.safe_load(p.read_text())) == []


def test_every_deliverable_is_filterable():
    from campaign_board import render_html
    out = render_html(_campaign())
    assert 'id="fbar"' in out
    for attr in ('data-who="alex data"', 'data-kind="fellowship public"', 'class="chip"'):
        assert attr in out


def test_flow_diagram_is_inlined_from_the_campaign_folder(tmp_path):
    from campaign_board import render_html
    (tmp_path / "flow.svg").write_text('<svg role="img"><title id="x">Flow</title></svg>')
    c = _campaign()
    c["campaign"]["diagram"] = "flow.svg"
    assert '<title id="x">Flow</title>' in render_html(c, base=tmp_path)


def test_old_source_name_is_refused():
    from campaign_board import validate
    c = _campaign()
    c["deliverables"][0]["source"] = "crt-q4"
    assert any("source" in e for e in validate(c))


def test_hidden_cards_really_disappear():
    """The grid layout overrode the hidden attribute, so filters counted but never hid."""
    from campaign_board import render_html
    assert "[hidden]{display:none!important}" in render_html(_campaign()).replace(" ", "")


def test_board_has_one_column_per_group():
    from campaign_board import render_html
    c = _campaign()
    c["columns"] = [{"name": "Fellowship Plan", "source": "fellowship"}, {"name": "Stories", "tracks": ["acme"]}]
    out = render_html(c)
    assert 'class="bcol"' in out and "Fellowship Plan" in out


def test_human_effort_is_summed_per_person():
    from campaign_board import validate, render_html
    c = _campaign()
    c["phases"] = [{"id": "P1", "name": "Prepare", "start": "2026-10-01", "end": "2026-10-31"}]
    c["human_effort"] = {"D-001": {"alex": 1.5}}
    c["recurring_human"] = [{"what": "board", "person": "alex", "hours_per_week": 1,
                             "start": "2026-10-05", "end": "2026-10-18"}]
    assert validate(c) == []
    assert "3.5" in render_html(c)  # 1.5 one-off + 2 weeks x 1h


def test_effort_for_unknown_item_or_agent_is_refused():
    from campaign_board import validate
    c = _campaign()
    c["human_effort"] = {"NOPE": {"alex": 1}, "D-001": {"data": 2}}
    errs = " ".join(validate(c))
    assert "NOPE" in errs and "data" in errs


def test_risk_and_infra_values_are_checked():
    from campaign_board import validate
    c = _campaign()
    c["risks"] = [{"id": "K1", "risk": "x", "likelihood": "often", "impact": "high", "mitigation": "y", "owner": "alex"}]
    c["infra"] = [{"part": "x", "confidence": 140}]
    errs = " ".join(validate(c))
    assert "often" in errs and "140" in errs


def test_type_chips_offer_every_deliverable_type():
    from campaign_board import render_html
    out = render_html(_campaign())
    assert 'data-g="type" data-v="blog"' in out


def test_streams_render_in_priority_order_with_fallback():
    from campaign_board import validate, render_html
    c = _campaign()
    c["streams"] = [
        {"id": "S1", "name": "Backbone", "start": "2026-10-01", "end": "2026-11-29",
         "source": "fellowship", "role": "first", "throttle": "never cut"},
        {"id": "S2", "name": "Extras", "start": "2026-10-15", "end": "2026-12-18",
         "tracks": ["acme"], "expands_when": "approvals keep up", "throttle": "cut first"},
    ]
    assert validate(c) == []
    out = render_html(c)
    assert out.index("Backbone") < out.index("Extras")
    assert "cut first" in out and 'class="gantt"' in out


def test_bad_stream_dates_are_refused():
    from campaign_board import validate
    c = _campaign()
    c["streams"] = [{"id": "S1", "name": "x", "start": "soon", "end": "2026-11-29"}]
    assert any("S1" in e for e in validate(c))


def test_cadences_render_by_frequency():
    from campaign_board import render_html
    c = _campaign()
    c["cadences"] = [{"every": "daily", "what": "Amplification queue", "who": "data", "output": "queue file",
                      "gate": "H2", "runs": "agent"}]
    c["workflows"] = [{"name": "A news item", "steps": ["An agent adds", "Alex picks"]}]
    out = render_html(c)
    assert "Amplification queue" in out and "Alex picks" in out
