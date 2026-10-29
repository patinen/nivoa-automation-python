from app.offline_demo import run_offline_demo


def test_offline_demo_exercises_http_worker_and_idempotent_replay(tmp_path):
    result = run_offline_demo(tmp_path / "isolated-demo")

    assert result["completed"] == 10
    assert result["sheet_rows"] == 10
    assert result["notifications"] == 6
    assert result["replay_created"] == 0
    assert result["replay_processed"] is False
