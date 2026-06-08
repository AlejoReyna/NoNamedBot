"""Tests for persisted and reconstructed positions."""

from __future__ import annotations

import json

from src.config.settings import Settings
from src.main import _load_positions_or_reconstruct, _portfolio_value_usdc
from src.strategy.position_manager import PositionManager


class FakeBalanceToolkit:
    """Minimal balance provider for reconstruction tests."""

    def get_balance(self, symbol: str | None = None) -> dict[str, object]:
        if symbol == "CAKE":
            return {"balances": {"CAKE": 12.5}}
        return {"balances": {symbol or "USDC": 0.0}}


class FakePortfolioToolkit:
    """Minimal stablecoin balance provider for portfolio value tests."""

    def get_balance(self, symbol: str | None = None) -> dict[str, object]:
        return {"balances": {symbol or "USDC": 9.0}}


def test_positions_persist_and_reload(tmp_path: object) -> None:
    state_path = tmp_path / "positions.json"  # type: ignore[operator]
    settings = Settings(position_state_path=str(state_path))
    manager = PositionManager(settings)

    manager.open_position("CAKE", amount_tokens=4.0, entry_price=2.5, entry_value_usdc=10.0)

    reloaded = PositionManager(settings)
    assert reloaded.load_positions() is True
    position = reloaded.get_position("CAKE")
    assert position is not None
    assert position.amount_tokens == 4.0
    assert position.entry_price == 2.5


def test_missing_position_state_initializes_empty_file(tmp_path: object) -> None:
    state_path = tmp_path / "positions.json"  # type: ignore[operator]
    settings = Settings(position_state_path=str(state_path))
    manager = PositionManager(settings)

    assert manager.load_positions() is False
    assert json.loads(state_path.read_text(encoding="utf-8")) == {"positions": []}


def test_missing_state_reconstructs_positions_from_wallet_balances(tmp_path: object) -> None:
    state_path = tmp_path / "missing-positions.json"  # type: ignore[operator]
    settings = Settings(paper_trade=False, position_state_path=str(state_path))
    manager = PositionManager(settings)

    reconstructed = _load_positions_or_reconstruct(
        manager,
        FakeBalanceToolkit(),  # type: ignore[arg-type]
        settings,
        {"CAKE": {"price": 2.0}},
    )

    position = manager.get_position("CAKE")
    assert reconstructed == 1
    assert position is not None
    assert position.amount_tokens == 12.5
    assert position.entry_value_usdc == 25.0
    assert state_path.exists()


def test_portfolio_value_includes_open_position_mark_value(tmp_path: object) -> None:
    state_path = tmp_path / "positions.json"  # type: ignore[operator]
    settings = Settings(paper_trade=False, position_state_path=str(state_path))
    manager = PositionManager(settings)
    manager.open_position("CAKE", amount_tokens=2.0, entry_price=1.0, entry_value_usdc=2.0)

    value = _portfolio_value_usdc(
        FakePortfolioToolkit(),  # type: ignore[arg-type]
        settings,
        {"CAKE": {"price": 3.0}},
        manager,
    )

    assert value == 15.0
