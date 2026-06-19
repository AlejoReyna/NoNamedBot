"""Robust adapter for the verified bnb-chain-agentkit tools."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from src.config.settings import Settings, load_settings
from src.config.tokens import TOKEN_CONTRACTS_BSC, resolve_twak_token

LOGGER = logging.getLogger(__name__)

try:
    from web3 import Web3
except ImportError:
    Web3 = None  # type: ignore[assignment]

try:
    from bnb_chain_agentkit.agent_toolkits import BnbChainToolkit
    from bnb_chain_agentkit.utils import BnbChainAPIWrapper
except ImportError:
    BnbChainToolkit = None  # type: ignore[assignment]
    BnbChainAPIWrapper = None  # type: ignore[assignment]


ERC20_BALANCE_ABI: list[dict[str, Any]] = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function",
    },
]


class BnbToolkitWrapper:
    """Adapter around read-only Web3 balances and live execution tools."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or load_settings()
        self.paper_trade = self.settings.paper_trade
        self.api_wrapper: Any | None = None
        self.toolkit: Any | None = None
        self.tools: list[Any] = []
        self.w3: Any | None = None

    def get_balance(self, symbol_or_account: str | None = None, token: str | None = None) -> dict[str, Any]:
        """Get a balance by symbol, or by explicit account/token for smoke tests."""

        account, balance_symbol, agentkit_token = self._resolve_balance_request(symbol_or_account, token)
        if self.paper_trade:
            return {
                "mode": "paper",
                "symbol": balance_symbol,
                "token": agentkit_token,
                "account": account,
                "balances": {balance_symbol: 10000.0},
                "portfolio_value_usdc": 10000.0,
            }
        return self._get_live_balance(account, balance_symbol, agentkit_token)

    def swap(self, from_symbol: str, to_symbol: str, amount: float, slippage_pct: float) -> dict[str, Any]:
        """Disable bnb-chain-agentkit swap execution."""

        raise RuntimeError("Swaps must be executed through TWAKInterface.swap")

    def transfer(self, to_address: str, symbol: str, amount: float) -> dict[str, Any]:
        """Transfer tokens through the agentkit transfer tool."""

        if self.paper_trade:
            return {
                "mode": "paper",
                "tool": "transfer",
                "to_address": to_address,
                "symbol": symbol.upper(),
                "amount": amount,
                "tx_hash": f"paper-transfer-{symbol.upper()}",
            }
        return self._invoke_live_tool(
            "transfer",
            {
                "recipient": to_address,
                "token": self._symbol_to_agentkit_token(symbol),
                "amount": self._amount_to_agentkit_string(amount),
            },
        )

    def _initialize_live_tools(self) -> None:
        if BnbChainAPIWrapper is None or BnbChainToolkit is None:
            raise RuntimeError(
                "bnb-chain-agentkit is required for live mode. Install requirements.txt before running --live."
            )

        self._prepare_agentkit_environment()
        self.api_wrapper = self._create_api_wrapper()
        self.toolkit = self._create_toolkit(self.api_wrapper)
        if hasattr(self.toolkit, "get_tools"):
            self.tools = list(self.toolkit.get_tools())
        else:
            self.tools = list(getattr(self.toolkit, "tools", []) or [])
        if not self.tools:
            raise RuntimeError("BnbChainToolkit did not expose any tools")

    def _create_api_wrapper(self) -> Any:
        try:
            return BnbChainAPIWrapper()
        except Exception as exc:
            raise RuntimeError(
                "Could not instantiate BnbChainAPIWrapper. Live mode requires configured "
                "agentkit credentials and BSC_PROVIDER_URL or BSC_RPC_URL in the environment."
            ) from exc

    @staticmethod
    def _create_toolkit(api_wrapper: Any) -> Any:
        if hasattr(BnbChainToolkit, "from_bnb_chain_api_wrapper"):
            return BnbChainToolkit.from_bnb_chain_api_wrapper(api_wrapper)
        try:
            return BnbChainToolkit(api_wrapper=api_wrapper)
        except TypeError:
            return BnbChainToolkit(api_wrapper)

    def _invoke_named_tool(self, name_fragment: str, payload: dict[str, Any]) -> dict[str, Any]:
        tool = self._find_tool(name_fragment)
        if hasattr(tool, "invoke"):
            result = tool.invoke(payload)
        elif hasattr(tool, "run"):
            result = tool.run(payload)
        elif callable(tool):
            result = tool(payload)
        else:
            raise RuntimeError(f"Tool {self._tool_name(tool)} cannot be invoked")
        if isinstance(result, dict):
            return result
        return {"raw": result}

    def _invoke_live_tool(self, name_fragment: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.tools:
            self._initialize_live_tools()
        return self._invoke_named_tool(name_fragment, payload)

    def _find_tool(self, name_fragment: str) -> Any:
        normalized = name_fragment.replace("_", "").lower()
        for tool in self.tools:
            tool_name = self._tool_name(tool).replace("_", "").lower()
            if normalized in tool_name:
                return tool
        available = ", ".join(self._tool_name(tool) for tool in self.tools) or "<none>"
        raise RuntimeError(f"Required bnb-chain-agentkit tool {name_fragment} is unavailable. Available: {available}")

    @staticmethod
    def _tool_name(tool: Any) -> str:
        return str(getattr(tool, "name", None) or getattr(tool, "__name__", None) or tool.__class__.__name__)

    def _prepare_agentkit_environment(self) -> None:
        if self.settings.bsc_rpc_url and not os.getenv("BSC_PROVIDER_URL"):
            os.environ["BSC_PROVIDER_URL"] = self.settings.bsc_rpc_url
        if self.settings.opbnb_provider_url and not os.getenv("OPBNB_PROVIDER_URL"):
            os.environ["OPBNB_PROVIDER_URL"] = self.settings.opbnb_provider_url
        if self.settings.wallet_address and not os.getenv("AGENT_WALLET_ADDRESS"):
            os.environ["AGENT_WALLET_ADDRESS"] = self.settings.wallet_address

    def _resolve_balance_request(self, symbol_or_account: str | None, token: str | None) -> tuple[str | None, str, str]:
        if token is None:
            token_or_symbol = symbol_or_account or self.settings.default_stable_symbol
            account = self.settings.wallet_address
        else:
            account = symbol_or_account or self.settings.wallet_address
            token_or_symbol = token

        agentkit_token = self._symbol_to_agentkit_token(token_or_symbol)
        balance_symbol = self._display_symbol_for_token(token_or_symbol)
        return account, balance_symbol, agentkit_token

    def _get_live_balance(self, account: str | None, symbol: str, token: str) -> dict[str, Any]:
        if not account:
            raise RuntimeError("AGENT_WALLET_ADDRESS or WALLET_ADDRESS is required for live balance checks")

        w3 = self._get_web3_client()
        checksum_account = w3.to_checksum_address(account)
        if token.upper() == "BNB":
            raw_balance = int(w3.eth.get_balance(checksum_account))
            amount = float(w3.from_wei(raw_balance, "ether"))
            return self._balance_payload(account, symbol, token, amount, raw_balance, 18)

        contract_address = self._resolve_erc20_contract_address(symbol, token)
        if contract_address is None:
            LOGGER.warning(
                "No verified BSC contract for %s (%s); reporting zero balance for reconciliation",
                symbol,
                token,
            )
            return self._balance_payload(account, symbol, token, 0.0, 0, 18)

        checksum_token = w3.to_checksum_address(contract_address)
        contract = w3.eth.contract(address=checksum_token, abi=ERC20_BALANCE_ABI)
        decimals = int(contract.functions.decimals().call())
        raw_balance = int(contract.functions.balanceOf(checksum_account).call())
        amount = raw_balance / (10**decimals)
        return self._balance_payload(account, symbol, token, amount, raw_balance, decimals)

    def get_transaction_receipt(self, tx_hash: str) -> dict[str, Any] | None:
        """Fetch a transaction receipt by hash, returning None if not available."""

        if self.paper_trade:
            return {"status": 1, "gasUsed": 0, "blockNumber": 0}
        w3 = self._get_web3_client()
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
            if receipt is None:
                return None
            return {
                "status": int(receipt.get("status", 1)) if isinstance(receipt, dict) else int(receipt.status),
                "gasUsed": int(receipt.get("gasUsed", 0)) if isinstance(receipt, dict) else int(receipt.gasUsed),
                "blockNumber": int(receipt.get("blockNumber", 0)) if isinstance(receipt, dict) else int(receipt.blockNumber),
            }
        except Exception:
            return None

    def _get_web3_client(self) -> Any:
        if self.w3 is not None:
            return self.w3
        if Web3 is None:
            raise RuntimeError("web3 is required for live balance checks. Install requirements.txt first.")
        if not self.settings.bsc_rpc_url:
            raise RuntimeError("BSC_PROVIDER_URL or BSC_RPC_URL is required for live balance checks")

        self.w3 = Web3(Web3.HTTPProvider(self.settings.bsc_rpc_url))
        is_connected = self.w3.is_connected if hasattr(self.w3, "is_connected") else self.w3.isConnected
        if not is_connected():
            raise ConnectionError(f"Could not connect to BSC RPC: {self.settings.bsc_rpc_url}")
        return self.w3

    @staticmethod
    def _balance_payload(
        account: str,
        symbol: str,
        token: str,
        amount: float,
        raw_balance: int,
        decimals: int,
    ) -> dict[str, Any]:
        return {
            "mode": "live",
            "account": account,
            "symbol": symbol,
            "token": token,
            "amount": amount,
            "balance": amount,
            "raw_balance": raw_balance,
            "decimals": decimals,
            "balances": {symbol: amount},
        }

    @staticmethod
    def _amount_to_agentkit_string(amount: float) -> str:
        return format(amount, ".18g")

    @staticmethod
    def _slippage_pct_to_bps(slippage_pct: float) -> int:
        return max(0, int(round(slippage_pct * 10_000)))

    @staticmethod
    def _normalize_balance_result(result: dict[str, Any], symbol: str, token: str) -> dict[str, Any]:
        raw = result.get("raw")
        if not isinstance(raw, str):
            return result

        match = re.search(r"\n(?P<balance>\d+)\s+\(decimals:\s*(?P<decimals>\d+)\)", raw)
        if match is None:
            return {"raw": raw, "symbol": symbol, "token": token}

        raw_balance = int(match.group("balance"))
        decimals = int(match.group("decimals"))
        amount = raw_balance / (10**decimals)
        return {
            "raw": raw,
            "symbol": symbol,
            "token": token,
            "amount": amount,
            "balance": amount,
            "raw_balance": raw_balance,
            "decimals": decimals,
            "balances": {symbol: amount},
        }

    @staticmethod
    def _resolve_erc20_contract_address(symbol: str, token: str) -> str | None:
        if BnbToolkitWrapper._is_hex_address(token):
            return token.strip()
        for key in (symbol.upper(), token.upper()):
            address = TOKEN_CONTRACTS_BSC.get(key)
            if address:
                return address
        return None

    @staticmethod
    def _symbol_to_agentkit_token(symbol: str) -> str:
        token = symbol.strip()
        if BnbToolkitWrapper._is_hex_address(token):
            return token
        normalized = token.upper()
        if normalized == "BNB":
            return "BNB"
        return resolve_twak_token(normalized)

    @staticmethod
    def _display_symbol_for_token(token_or_symbol: str) -> str:
        token = token_or_symbol.strip()
        if not BnbToolkitWrapper._is_hex_address(token):
            return token.upper()

        normalized_token = token.lower()
        for symbol, address in TOKEN_CONTRACTS_BSC.items():
            if address.lower() == normalized_token:
                return symbol
        return token

    @staticmethod
    def _is_hex_address(value: str) -> bool:
        return bool(re.fullmatch(r"0x[a-fA-F0-9]{40}", value.strip()))
