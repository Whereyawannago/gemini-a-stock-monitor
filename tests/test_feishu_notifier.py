"""Tests for Feishu webhook HMAC-SHA256 signing and 3-color interactive cards."""

from __future__ import annotations

import base64
import hashlib
import hmac
from unittest.mock import MagicMock, patch

from stock_monitor.alert_engine import AlertEngine
from stock_monitor.config import Settings
from stock_monitor.feishu_notifier import (
    FeishuNotifier,
    build_feishu_card_payload,
    generate_feishu_sign,
)
from stock_monitor.llm_analyzer import build_rule_based_summary
from stock_monitor.mock_data import build_mock_snapshot
from stock_monitor.models import AnalysisResult


def test_generate_feishu_sign_matches_spec() -> None:
    ts = 1732579200
    secret = "my_feishu_secret"
    expected = base64.b64encode(
        hmac.new(f"{ts}\n{secret}".encode("utf-8"), digestmod=hashlib.sha256).digest()
    ).decode("utf-8")
    assert generate_feishu_sign(ts, secret) == expected


def test_card_colors_for_normal_surge_plunge(tmp_path) -> None:
    settings = Settings(state_dir=tmp_path)
    engine = AlertEngine(settings)

    expected_colors = {
        "normal": "blue",
        "surge": "red",
        "plunge": "green",
    }
    for scenario, color in expected_colors.items():
        snap = build_mock_snapshot(scenario)  # type: ignore[arg-type]
        alert = engine.evaluate(snap, persist=False)
        analysis = AnalysisResult(
            content=build_rule_based_summary(snap, alert),
            source="rule_fallback",
            model="rule_engine",
        )
        payload = build_feishu_card_payload(
            snap, alert, analysis, secret="sec123", timestamp=1732579200
        )
        assert payload["msg_type"] == "interactive"
        assert payload["card"]["header"]["template"] == color
        assert payload["timestamp"] == "1732579200"
        assert payload["sign"] == generate_feishu_sign(1732579200, "sec123")


def test_feishu_notifier_send_posts_payload(tmp_path) -> None:
    settings = Settings(
        feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/mock",
        feishu_webhook_secret="sec_test",
        state_dir=tmp_path,
    )
    snap = build_mock_snapshot("normal")
    alert = AlertEngine(settings).evaluate(snap, persist=False)
    analysis = AnalysisResult(content="测试内容", source="rule_fallback", model="rule_engine")

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"code": 0, "msg": "success"}
    mock_resp.raise_for_status.return_value = None

    with patch("stock_monitor.feishu_notifier.requests.post", return_value=mock_resp) as mock_post:
        res = FeishuNotifier(settings).send(snap, alert, analysis, dry_run=False)
        assert res["dry_run"] is False
        assert mock_post.called
        sent_json = mock_post.call_args.kwargs["json"]
        assert sent_json["msg_type"] == "interactive"
        assert "sign" in sent_json
