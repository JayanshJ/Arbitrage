"""Config-driven symbol mapping between exchanges and unified internal symbols."""

from __future__ import annotations

import json
from pathlib import Path
from dataclasses import dataclass

from .ticker import Exchange

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "symbols.json"


@dataclass
class SymbolMapping:
    """Bidirectional mapping between exchange-native and unified symbols."""

    # exchange_name -> { native_symbol -> unified_symbol }
    to_unified: dict[str, dict[str, str]]
    # exchange_name -> { unified_symbol -> native_symbol }
    to_native: dict[str, dict[str, str]]
    # All unified symbols we track
    unified_symbols: list[str]

    @classmethod
    def from_config(cls, path: Path = CONFIG_PATH) -> SymbolMapping:
        with open(path) as f:
            data = json.load(f)

        to_unified: dict[str, dict[str, str]] = {}
        to_native: dict[str, dict[str, str]] = {}
        unified_symbols: list[str] = []

        for exchange in Exchange:
            to_unified[exchange.value] = {}
            to_native[exchange.value] = {}

        for pair in data["pairs"]:
            unified = pair["unified"]
            unified_symbols.append(unified)

            for exchange in Exchange:
                native = pair.get(exchange.value)
                if native:
                    to_unified[exchange.value][native.lower()] = unified
                    to_native[exchange.value][unified] = native

        return cls(
            to_unified=to_unified,
            to_native=to_native,
            unified_symbols=unified_symbols,
        )

    def get_unified(self, exchange: Exchange, native_symbol: str) -> str | None:
        return self.to_unified.get(exchange.value, {}).get(native_symbol.lower())

    def get_native(self, exchange: Exchange, unified_symbol: str) -> str | None:
        return self.to_native.get(exchange.value, {}).get(unified_symbol)

    def get_native_symbols(self, exchange: Exchange) -> list[str]:
        return list(self.to_native.get(exchange.value, {}).values())
