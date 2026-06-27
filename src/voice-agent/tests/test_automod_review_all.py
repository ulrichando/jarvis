"""Tests for the batch review-council path (review_council --all)."""
import json


def _proposal(ad, pid, status="pending", diff="d", intent="i"):
    (ad / f"{pid}.json").write_text(json.dumps(
        {"id": pid, "status": status, "diff": diff, "intent": intent}))


def test_pending_ids_only_pending_skips_review_files(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ad = tmp_path / "auto-mods"
    ad.mkdir(parents=True)
    _proposal(ad, "automod-2026-01-01-aaa", status="pending")
    _proposal(ad, "automod-2026-01-01-bbb", status="failed")
    (ad / "automod-2026-01-01-aaa.review.json").write_text(json.dumps({"overall": {}}))
    from pipeline.automod import review_council
    assert review_council._pending_ids() == ["automod-2026-01-01-aaa"]


def test_review_all_pending_reviews_each_in_parallel(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ad = tmp_path / "auto-mods"
    ad.mkdir(parents=True)
    _proposal(ad, "automod-2026-01-01-aaa", diff="diff-a")
    _proposal(ad, "automod-2026-01-01-bbb", diff="diff-b")
    _proposal(ad, "automod-2026-01-01-ccc", status="failed")  # not pending → skipped

    from pipeline.automod import review_council
    seen = []

    def fake_review(aid, diff, intent):
        seen.append((aid, diff))
        return {"overall": {"verdict": "pass"}}

    monkeypatch.setattr(review_council, "review_proposal", fake_review)
    summary = review_council.review_all_pending(concurrency=2)

    assert summary["count"] == 2
    assert summary["reviewed"] == 2
    assert summary["failed"] == 0
    assert {aid for aid, _ in seen} == {"automod-2026-01-01-aaa", "automod-2026-01-01-bbb"}
    assert ("automod-2026-01-01-aaa", "diff-a") in seen
    assert all(r["verdict"] == "pass" for r in summary["results"])


def test_review_all_pending_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "auto-mods").mkdir(parents=True)
    from pipeline.automod import review_council
    summary = review_council.review_all_pending(concurrency=2)
    assert summary == {"count": 0, "reviewed": 0, "failed": 0, "concurrency": 2, "results": []}


def test_main_all_dispatches(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    (tmp_path / "auto-mods").mkdir(parents=True)
    from pipeline.automod import review_council
    called = {}
    monkeypatch.setattr(review_council, "review_all_pending",
                        lambda: called.setdefault("hit", {"count": 0}) or {"count": 0})
    rc = review_council._main(["--all"])
    assert rc == 0
    assert called.get("hit") is not None
    assert json.loads(capsys.readouterr().out)["count"] == 0


def test_review_all_pending_writes_progress_status(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_HOME", str(tmp_path))
    ad = tmp_path / "auto-mods"
    ad.mkdir(parents=True)
    _proposal(ad, "automod-2026-01-01-aaa")
    _proposal(ad, "automod-2026-01-01-bbb")
    from pipeline.automod import review_council
    monkeypatch.setattr(review_council, "review_proposal", lambda *a: {"overall": {"verdict": "pass"}})
    review_council.review_all_pending(concurrency=2)
    status = json.loads((ad / ".review-all-status.json").read_text())
    assert status["running"] is False
    assert status["total"] == 2 and status["done"] == 2
    assert "finished_at" in status
