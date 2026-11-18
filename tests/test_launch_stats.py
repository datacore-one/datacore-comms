import launch_stats as ls


def test_youtube_counts_parse_strings():
    assert ls.yt_row({"statistics": {"viewCount": "12", "likeCount": "3"}}) == {
        "views": 12, "likes": 3, "comments": 0}


def test_x_prefers_private_impressions():
    row = ls.x_row({"public_metrics": {"impression_count": 5, "like_count": 1},
                    "non_public_metrics": {"impression_count": 40}})
    assert row["views"] == 40 and row["likes"] == 1


def test_x_falls_back_to_public_impressions():
    assert ls.x_row({"public_metrics": {"impression_count": 7}})["views"] == 7


def test_linkedin_summary():
    assert ls.li_row({"likesSummary": {"totalLikes": 4},
                      "commentsSummary": {"aggregatedTotalComments": 2}}) == {"likes": 4, "comments": 2}


def test_refusal_is_reported_not_zero(monkeypatch):
    monkeypatch.setattr(ls, "youtube", lambda s: (_ for _ in ()).throw(RuntimeError("no scope")))
    import io, json, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ls.main(["--youtube", "plur:abc"])
    row = json.loads(buf.getvalue())[0]
    assert "views" not in row and row["error"].startswith("not readable")
