"""Tests for TelegramNotifier sidecar.

All tests use unittest.mock to avoid real Telegram API calls.
"""

from __future__ import annotations

import json
import urllib.parse
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.common.telegram_notifier import TelegramNotifier

BOT_TOKEN = "123456:TEST_TOKEN"
CHAT_ID = "987654321"


@pytest.fixture
def notifier() -> TelegramNotifier:
    return TelegramNotifier(BOT_TOKEN, CHAT_ID)


class TestTelegramNotifierLifecycle:
    def test_disabled_when_no_token(self) -> None:
        n = TelegramNotifier(None, CHAT_ID)
        assert not n._enabled

    def test_disabled_when_no_chat_id(self) -> None:
        n = TelegramNotifier(BOT_TOKEN, None)
        assert not n._enabled

    def test_enabled_when_both_set(self, notifier: TelegramNotifier) -> None:
        assert notifier._enabled


class TestNotifyBuy:
    @patch("urllib.request.urlopen")
    def test_sends_buy_message(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        notifier.notify_buy(
            symbol="CAKE",
            amount_usdc=1.84,
            price=0.786,
            tx_hash="0x5cbbdce2a5940578d129ede506765d5fdbd383d4c6ca5a2800296d893b0d6ad7",
            regime="trending_up",
            entry_score=68.5,
            daily_trade_count=2,
            max_daily=3,
            slippage_pct=0.0045,
        )
        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        assert call_args is not None
        url = call_args[0][0]
        assert url == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        body = call_args[1]["data"]
        payload = urllib.parse.parse_qs(body.decode("utf-8"))
        assert payload["chat_id"] == [CHAT_ID]
        assert "CAKE" in payload["text"][0]
        assert "BUY EXECUTED" in payload["text"][0]
        assert payload["parse_mode"] == ["Markdown"]

    @patch("urllib.request.urlopen")
    def test_noop_when_disabled(self, mock_urlopen: MagicMock) -> None:
        n = TelegramNotifier(None, None)
        n.notify_buy(
            symbol="CAKE",
            amount_usdc=1.0,
            price=1.0,
            tx_hash=None,
            regime="ranging",
            entry_score=None,
            daily_trade_count=0,
            max_daily=3,
        )
        mock_urlopen.assert_not_called()


class TestNotifyBnbMomentum:
    @patch("urllib.request.urlopen")
    def test_rate_limits_momentum(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        notifier.notify_bnb_momentum(
            bnb_1h=0.012,
            bnb_6h=0.034,
            bnb_24h=0.051,
            regime="trending_up",
            score=4.2,
        )
        assert mock_urlopen.call_count == 1
        notifier.notify_bnb_momentum(
            bnb_1h=0.015,
            bnb_6h=0.040,
            bnb_24h=0.060,
            regime="trending_up",
            score=4.5,
        )
        assert mock_urlopen.call_count == 1

    @patch("urllib.request.urlopen")
    def test_momentum_message_format(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        notifier.notify_bnb_momentum(
            bnb_1h=0.012,
            bnb_6h=0.034,
            bnb_24h=0.051,
            regime="trending_up",
            score=4.2,
            breadth=0.72,
        )
        call_args = mock_urlopen.call_args
        assert call_args is not None
        body = call_args[1]["data"]
        payload = urllib.parse.parse_qs(body.decode("utf-8"))
        text = payload["text"][0]
        assert "🚀" in text
        assert "BNB MOMENTUM DETECTED" in text
        assert "+1.20%" in text
        assert "72%" in text


class TestNotifyX402Balance:
    @patch("src.common.telegram_notifier.TelegramNotifier._fetch_x402_view")
    @patch("urllib.request.urlopen")
    def test_low_balance_alert(self, mock_urlopen: MagicMock, mock_fetch: MagicMock, notifier: TelegramNotifier) -> None:
        view = MagicMock()
        view.usdc_balance = Decimal("0.80")
        mock_fetch.return_value = view

        notifier.notify_x402_balance_if_changed(
            cycle_x402_cost=0.015,
            daily_spend_usdc=0.50,
            total_budget_usdc=5.0,
            daily_budget_usdc=1.0,
        )
        mock_urlopen.assert_called_once()
        body = mock_urlopen.call_args[1]["data"]
        payload = urllib.parse.parse_qs(body.decode("utf-8"))
        assert "⚠️" in payload["text"][0]
        assert "x402 DATA WALLET LOW" in payload["text"][0]

    @patch("src.common.telegram_notifier.TelegramNotifier._fetch_x402_view")
    @patch("urllib.request.urlopen")
    def test_no_alert_when_healthy_and_small_spend(
        self, mock_urlopen: MagicMock, mock_fetch: MagicMock, notifier: TelegramNotifier
    ) -> None:
        view = MagicMock()
        view.usdc_balance = Decimal("3.50")
        mock_fetch.return_value = view

        notifier._prev_x402_balance = Decimal("3.55")
        notifier.notify_x402_balance_if_changed(
            cycle_x402_cost=0.01,
            daily_spend_usdc=0.10,
            total_budget_usdc=5.0,
            daily_budget_usdc=1.0,
        )
        mock_urlopen.assert_not_called()

    @patch("src.common.telegram_notifier.TelegramNotifier._fetch_x402_view")
    @patch("urllib.request.urlopen")
    def test_alert_on_first_run_or_big_spend(
        self, mock_urlopen: MagicMock, mock_fetch: MagicMock, notifier: TelegramNotifier
    ) -> None:
        view = MagicMock()
        view.usdc_balance = Decimal("3.50")
        mock_fetch.return_value = view

        notifier.notify_x402_balance_if_changed(
            cycle_x402_cost=0.60,
            daily_spend_usdc=0.60,
            total_budget_usdc=5.0,
            daily_budget_usdc=1.0,
        )
        mock_urlopen.assert_called_once()
        body = mock_urlopen.call_args[1]["data"]
        payload = urllib.parse.parse_qs(body.decode("utf-8"))
        assert "💰" in payload["text"][0]
        assert "x402 Data Wallet Update" in payload["text"][0]


class TestNotifyRiskEvent:
    @patch("urllib.request.urlopen")
    def test_kill_switch_message(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        notifier.notify_risk_event(
            event_type="KILL_SWITCH",
            portfolio_value=15.40,
            drawdown_pct=18.5,
            details="liquidating all positions",
        )
        body = mock_urlopen.call_args[1]["data"]
        payload = urllib.parse.parse_qs(body.decode("utf-8"))
        assert "🛑" in payload["text"][0]
        assert "KILL_SWITCH" in payload["text"][0]

    @patch("urllib.request.urlopen")
    def test_warning_message(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        notifier.notify_risk_event(
            event_type="HALT",
            portfolio_value=18.20,
            drawdown_pct=12.0,
            details="manual halt active",
        )
        body = mock_urlopen.call_args[1]["data"]
        payload = urllib.parse.parse_qs(body.decode("utf-8"))
        assert "⚠️" in payload["text"][0]
        assert "HALT" in payload["text"][0]


class TestNotifyDailyLimit:
    @patch("urllib.request.urlopen")
    def test_daily_limit_message(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        notifier.notify_daily_limit(
            daily_trade_count=3,
            max_daily=3,
            portfolio_value=20.0,
        )
        body = mock_urlopen.call_args[1]["data"]
        payload = urllib.parse.parse_qs(body.decode("utf-8"))
        assert "📛" in payload["text"][0]
        assert "DAILY TRADE LIMIT REACHED" in payload["text"][0]
        assert "3/3" in payload["text"][0]

    @patch("urllib.request.urlopen")
    def test_rate_limits_daily_limit(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        notifier.notify_daily_limit(daily_trade_count=3, max_daily=3, portfolio_value=20.0)
        assert mock_urlopen.call_count == 1
        notifier.notify_daily_limit(daily_trade_count=3, max_daily=3, portfolio_value=20.0)
        assert mock_urlopen.call_count == 1


class TestFailSafety:
    @patch("urllib.request.urlopen", side_effect=Exception("network timeout"))
    def test_buy_does_not_raise_on_failure(self, mock_urlopen: MagicMock, notifier: TelegramNotifier) -> None:
        notifier.notify_buy(
            symbol="CAKE",
            amount_usdc=1.0,
            price=1.0,
            tx_hash=None,
            regime="ranging",
            entry_score=None,
            daily_trade_count=0,
            max_daily=3,
        )
        error_path = Path("logs/telegram_errors.jsonl")
        if error_path.exists():
            lines = error_path.read_text().strip().splitlines()
            assert len(lines) > 0
            record = json.loads(lines[-1])
            assert "network timeout" in record["error"]


class TestErrorLogging:
    def test_log_error_creates_file(self, notifier: TelegramNotifier) -> None:
        error_path = Path("logs/telegram_errors.jsonl")
        if error_path.exists():
            error_path.unlink()
        notifier._log_error("test message", Exception("test exc"))
        assert error_path.exists()
        lines = error_path.read_text().strip().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["error"] == "test exc"
        assert "test message" in record["message_preview"]
