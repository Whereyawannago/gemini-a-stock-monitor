"""End-to-end integration tests for --mock and --dry-run CLI execution."""

from __future__ import annotations

import pytest

from stock_monitor.main import main


@pytest.mark.parametrize(
    "scenario,expected_template",
    [
        ("normal", '"template": "blue"'),
        ("surge", '"template": "red"'),
        ("plunge", '"template": "green"'),
    ],
)
def test_cli_mock_dry_run_scenarios(
    scenario: str,
    expected_template: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("STATE_DIR", str(tmp_path))
    monkeypatch.setenv("MINIMAX_API_KEY", "")
    monkeypatch.setenv("FEISHU_WEBHOOK_URL", "")

    exit_code = main(
        [
            "--once",
            "--mock",
            "--mock-scenario",
            scenario,
            "--dry-run",
            "--print-json",
        ]
    )
    captured = capsys.readouterr().out
    assert exit_code == 0
    assert expected_template in captured
    assert "上证指数" in captured
    assert "深证成指" in captured
    assert "创业板指" in captured
    assert "科创50" in captured
    assert "一、大盘与量能格局" in captured
