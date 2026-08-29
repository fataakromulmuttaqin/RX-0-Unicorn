"""
Tests untuk Phase 4 Alert System — RX-0 Unicorn.

Covers:
    - CooldownManager: allow/block/mark/cleanup/clear logic
    - format_signal: long/short/A+/valid/skip, edge cases (no entry, no signals)
    - TelegramBot: graceful degradation tanpa token, mock httpx call dengan token

Total: ~24 tests. Semua test isolated (tmp_path untuk SQLite, monkeypatch
untuk env vars, mocker untuk httpx).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# =============================================================================
# CooldownManager tests
# =============================================================================


class TestCooldownManager:
    """SQLite-backed per-pair alert cooldown."""

    def _make(self, tmp_path: Path, cooldown_minutes: int = 15):
        from alerts.cooldown import CooldownManager

        return CooldownManager(
            cooldown_minutes=cooldown_minutes,
            db_path=tmp_path / "test_cooldown.db",
        )

    def test_table_created_on_init(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        # Tabel harus ada
        with sqlite3.connect(str(cd.db_path)) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='alert_cooldown'"
            ).fetchone()
        assert row is not None, "alert_cooldown table harus dibuat otomatis"

    def test_should_alert_new_pair(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        assert cd.should_alert("BTC/USDT") is True

    def test_mark_then_block_within_cooldown(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path, cooldown_minutes=15)
        cd.mark_alerted("BTC/USDT")
        assert cd.should_alert("BTC/USDT") is False

    def test_should_alert_after_cooldown_expires(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path, cooldown_minutes=15)
        # Mark 16 menit yang lalu
        old_ts = int(datetime.now(timezone.utc).timestamp()) - (16 * 60)
        cd.mark_alerted("BTC/USDT", ts=old_ts)
        assert cd.should_alert("BTC/USDT") is True

    def test_cooldown_zero_always_allows(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path, cooldown_minutes=0)
        cd.mark_alerted("BTC/USDT")
        assert cd.should_alert("BTC/USDT") is True

    def test_different_pairs_independent(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        cd.mark_alerted("BTC/USDT")
        assert cd.should_alert("BTC/USDT") is False
        assert cd.should_alert("ETH/USDT") is True

    def test_mark_alerted_is_upsert(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        cd.mark_alerted("BTC/USDT", ts=1000)
        assert cd.get_last_alert_at("BTC/USDT") == 1000
        cd.mark_alerted("BTC/USDT", ts=2000)
        assert cd.get_last_alert_at("BTC/USDT") == 2000

    def test_clear_specific_pair(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        cd.mark_alerted("BTC/USDT")
        cd.mark_alerted("ETH/USDT")
        deleted = cd.clear("BTC/USDT")
        assert deleted == 1
        assert cd.should_alert("BTC/USDT") is True
        assert cd.should_alert("ETH/USDT") is False

    def test_clear_all(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        cd.mark_alerted("BTC/USDT")
        cd.mark_alerted("ETH/USDT")
        cd.mark_alerted("SOL/USDT")
        deleted = cd.clear()
        assert deleted == 3
        assert cd.all_pairs() == {}

    def test_cleanup_old_removes_stale(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        now = int(datetime.now(timezone.utc).timestamp())
        # Fresh: now - 1h
        cd.mark_alerted("BTC/USDT", ts=now - 3600)
        # Stale: 2 days ago
        cd.mark_alerted("ETH/USDT", ts=now - (2 * 24 * 3600))
        # Stale: 25h ago
        cd.mark_alerted("SOL/USDT", ts=now - (25 * 3600))
        deleted = cd.cleanup_old(max_age_hours=24)
        assert deleted == 2
        assert "BTC/USDT" in cd.all_pairs()
        assert "ETH/USDT" not in cd.all_pairs()
        assert "SOL/USDT" not in cd.all_pairs()

    def test_get_last_alert_at_returns_none_for_unknown(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        assert cd.get_last_alert_at("UNKNOWN/USDT") is None

    def test_all_pairs(self, tmp_path: Path) -> None:
        cd = self._make(tmp_path)
        cd.mark_alerted("BTC/USDT", ts=100)
        cd.mark_alerted("ETH/USDT", ts=200)
        pairs = cd.all_pairs()
        assert pairs == {"BTC/USDT": 100, "ETH/USDT": 200}

    def test_context_manager(self, tmp_path: Path) -> None:
        from alerts.cooldown import CooldownManager

        with CooldownManager(
            cooldown_minutes=10, db_path=tmp_path / "ctx.db"
        ) as cd:
            cd.mark_alerted("BTC/USDT")
            assert cd.should_alert("BTC/USDT") is False
        # Setelah exit, koneksi harus ditutup (tidak ada error)


# =============================================================================
# format_signal tests
# =============================================================================


def _long_a_plus() -> dict:
    return {
        "close": 62450.0,
        "regime": "trending",
        "direction": "long",
        "score": 4,
        "grade": "A+",
        "size_multiplier": 1.5,
        "entry_price": 62450.0,
        "stop_loss": 62180.0,
        "take_profit_1": 62990.0,
        "take_profit_2": 63530.0,
        "risk_reward": 2.0,
        "signals": {
            "luminance": 1,
            "rsi_regime": 1,
            "structure": 1,
            "wavetrend": 1,
        },
    }


def _long_valid() -> dict:
    return {
        "close": 62450.0,
        "regime": "trending",
        "direction": "long",
        "score": 3,
        "grade": "valid",
        "size_multiplier": 1.0,
        "entry_price": 62450.0,
        "stop_loss": 62180.0,
        "take_profit_1": 62990.0,
        "take_profit_2": 63530.0,
        "risk_reward": 2.0,
        "signals": {
            "luminance": 1,
            "rsi_regime": 1,
            "structure": 1,
            "wavetrend": 0,
        },
    }


def _short_a_plus() -> dict:
    return {
        "close": 3450.0,
        "regime": "ranging",
        "direction": "short",
        "score": 4,
        "grade": "A+",
        "size_multiplier": 1.5,
        "entry_price": 3450.0,
        "stop_loss": 3520.0,
        "take_profit_1": 3310.0,
        "take_profit_2": 3170.0,
        "risk_reward": 2.0,
        "signals": {
            "luminance": -1,
            "rsi_regime": -1,
            "structure": -1,
            "wavetrend": -1,
        },
    }


def _skip() -> dict:
    return {
        "close": 100.0,
        "regime": "ranging",
        "direction": "long",
        "score": 2,
        "grade": "skip",
        "size_multiplier": 0.0,
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "take_profit_1": 110.0,
        "take_profit_2": 120.0,
        "risk_reward": 2.0,
        "signals": {
            "luminance": 1,
            "rsi_regime": 0,
            "structure": 1,
            "wavetrend": 0,
        },
    }


class TestFormatSignal:
    """format_signal: handle long/short/A+/valid/skip + edge cases."""

    def test_long_a_plus_returns_string(self) -> None:
        from alerts.formatter import format_signal

        text = format_signal(_long_a_plus(), pair="BTC/USDT", timeframe="1H")
        assert isinstance(text, str)
        assert "RX-0 SIGNAL" in text
        assert "A+" in text
        assert "LONG" in text
        assert "BTC/USDT" in text

    def test_long_a_plus_uses_star_emoji(self) -> None:
        from alerts.formatter import format_signal

        text = format_signal(_long_a_plus(), pair="BTC/USDT", timeframe="1H")
        assert text.startswith("⭐")

    def test_long_valid_uses_green_emoji(self) -> None:
        from alerts.formatter import format_signal

        text = format_signal(_long_valid(), pair="ETH/USDT", timeframe="4H")
        assert text.startswith("🟢")
        assert "VALID" in text
        assert "3/4" in text

    def test_short_a_plus_renders_correctly(self) -> None:
        from alerts.formatter import format_signal

        text = format_signal(_short_a_plus(), pair="ETH/USDT", timeframe="1H")
        assert text is not None
        assert "SHORT" in text
        # Untuk short, SL di ATAS entry -> pct positif
        # 70/3450 = ~2.03%
        assert "+2.03%" in text
        # TP di BAWAH entry -> pct negatif
        # 140/3450 = ~4.06% (TP1)
        assert "-4.06%" in text

    def test_skip_returns_none(self) -> None:
        from alerts.formatter import format_signal

        assert format_signal(_skip(), pair="X/USDT", timeframe="1H") is None

    def test_skip_with_no_direction_returns_none(self) -> None:
        from alerts.formatter import format_signal

        result = dict(_long_valid())
        result["direction"] = None
        assert format_signal(result, pair="X/USDT", timeframe="1H") is None

    def test_no_entry_price_returns_string_with_na(self) -> None:
        from alerts.formatter import format_signal

        result = _long_valid()
        result["entry_price"] = None
        result["stop_loss"] = None
        result["take_profit_1"] = None
        result["take_profit_2"] = None
        text = format_signal(result, pair="X/USDT", timeframe="1H")
        assert text is not None
        assert "Entry:      N/A" in text
        assert "SL:" in text
        assert "(N/A)" in text

    def test_no_signals_renders_placeholder(self) -> None:
        from alerts.formatter import format_signal

        result = _long_valid()
        result["signals"] = {}
        text = format_signal(result, pair="X/USDT", timeframe="1H")
        assert text is not None
        assert "(no signal data)" in text

    def test_uses_symbol_from_result_if_pair_not_given(self) -> None:
        from alerts.formatter import format_signal

        result = _long_valid()
        result["symbol"] = "SOL/USDT"
        text = format_signal(result, timeframe="1H")
        assert text is not None
        assert "SOL/USDT" in text

    def test_confluence_section_uses_check_and_cross(self) -> None:
        from alerts.formatter import format_signal

        result = _long_valid()  # wavetrend = 0 -> ✗
        text = format_signal(result, pair="BTC/USDT", timeframe="1H")
        assert "✓ Luminance breakout" in text
        assert "✓ RSI regime aligned" in text
        assert "✓ BOS confirm" in text
        assert "✗ WaveTrend" in text

    def test_wavetrend_active_uses_cross_label(self) -> None:
        from alerts.formatter import format_signal

        # All signals active -> "WaveTrend cross" bukan "(no cross)"
        result = _long_a_plus()
        text = format_signal(result, pair="BTC/USDT", timeframe="1H")
        assert "WaveTrend cross" in text
        assert "no cross" not in text

    def test_unknown_grade_returns_none(self) -> None:
        from alerts.formatter import format_signal

        result = _long_valid()
        result["grade"] = "bogus"
        assert format_signal(result, pair="X/USDT", timeframe="1H") is None

    def test_grade_normalized_from_lowercase(self) -> None:
        from alerts.formatter import format_signal

        result = _long_a_plus()
        result["grade"] = "a+"
        text = format_signal(result, pair="BTC/USDT", timeframe="1H")
        assert text is not None
        assert "A+" in text


# =============================================================================
# TelegramBot tests
# =============================================================================


class TestTelegramBot:
    """TelegramBot: graceful degradation + mocked httpx call."""

    def test_no_token_means_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from alerts.telegram import TelegramBot

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        bot = TelegramBot()
        assert bot.is_configured is False
        bot.close()

    def test_only_token_without_chat_id_not_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from alerts.telegram import TelegramBot

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc:123")
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        bot = TelegramBot()
        assert bot.is_configured is False
        bot.close()

    def test_explicit_args_override_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from alerts.telegram import TelegramBot

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "envtoken")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
        bot = TelegramBot(token="argtoken", chat_id="222")
        assert bot.token == "argtoken"
        assert bot.chat_id == "222"
        assert bot.is_configured is True
        bot.close()

    def test_send_message_returns_false_when_degraded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from alerts.telegram import TelegramBot

        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
        bot = TelegramBot()
        result = bot.send_message("hello")
        assert result is False
        bot.close()

    def test_send_message_makes_httpx_call_when_configured(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from alerts.telegram import TelegramBot

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST:TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 1}}

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            bot = TelegramBot()
            ok = bot.send_message("test message")
            assert ok is True
            # Verify httpx dipanggil dengan URL yang benar
            called_url = mock_client.post.call_args[0][0]
            assert "TEST:TOKEN" in called_url
            payload = mock_client.post.call_args[1]["json"]
            assert payload["chat_id"] == "12345"
            assert payload["text"] == "test message"
            bot.close()

    def test_send_message_handles_api_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from alerts.telegram import TelegramBot

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST:TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"ok": false, "description": "bad chat id"}'

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            bot = TelegramBot()
            ok = bot.send_message("test")
            assert ok is False
            bot.close()

    def test_send_message_handles_network_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import httpx

        from alerts.telegram import TelegramBot

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST:TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

        mock_client = MagicMock()
        mock_client.post.side_effect = httpx.ConnectError("boom")

        with patch("httpx.Client", return_value=mock_client):
            bot = TelegramBot()
            ok = bot.send_message("test")
            assert ok is False
            bot.close()

    def test_parse_mode_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from alerts.telegram import TelegramBot

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response

        with patch("httpx.Client", return_value=mock_client):
            bot = TelegramBot()
            bot.send_message("**bold**", parse_mode="Markdown")
            payload = mock_client.post.call_args[1]["json"]
            assert payload["parse_mode"] == "Markdown"
            bot.close()

    def test_context_manager(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from alerts.telegram import TelegramBot

        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "T")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
        with TelegramBot() as bot:
            assert bot.is_configured is True
