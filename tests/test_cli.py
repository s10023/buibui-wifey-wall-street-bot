"""Tests for wifey.py CLI argument parsing and dispatch."""

from unittest.mock import patch

import pytest


class TestCLIParsing:
    """Tests for CLI argument parsing in wifey.py."""

    def test_price_subcommand_defaults(self) -> None:
        """Price subcommand parses with correct defaults."""
        from monitor import price_monitor

        with patch.object(price_monitor, "main") as mock_main:
            with patch("sys.argv", ["wifey.py", "monitor", "price"]):
                from wifey import main

                main()

            mock_main.assert_called_once_with(
                live=False, telegram=False, sort="default"
            )

    def test_price_with_live_flag(self) -> None:
        """Price subcommand with --live flag."""
        from monitor import price_monitor

        with patch.object(price_monitor, "main") as mock_main:
            with patch("sys.argv", ["wifey.py", "monitor", "price", "--live"]):
                from wifey import main

                main()

            mock_main.assert_called_once_with(live=True, telegram=False, sort="default")

    def test_price_with_sort(self) -> None:
        """Price subcommand with --sort flag."""
        from monitor import price_monitor

        with patch.object(price_monitor, "main") as mock_main:
            with patch(
                "sys.argv",
                ["wifey.py", "monitor", "price", "--sort", "change_15m:desc"],
            ):
                from wifey import main

                main()

            mock_main.assert_called_once_with(
                live=False, telegram=False, sort="change_15m:desc"
            )

    def test_position_subcommand_defaults(self) -> None:
        """Position subcommand parses with correct defaults."""
        from monitor import position_monitor

        with patch.object(position_monitor, "main") as mock_main:
            with patch("sys.argv", ["wifey.py", "monitor", "position"]):
                from wifey import main

                main()

            mock_main.assert_called_once_with(
                sort="default",
                telegram=False,
                hide_empty=False,
                compact=False,
                live=False,
            )

    def test_position_with_all_flags(self) -> None:
        """Position subcommand with all flags."""
        from monitor import position_monitor

        with patch.object(position_monitor, "main") as mock_main:
            with patch(
                "sys.argv",
                [
                    "wifey.py",
                    "monitor",
                    "position",
                    "--sort",
                    "pnl_pct:desc",
                    "--telegram",
                    "--hide-empty",
                    "--compact",
                    "--live",
                ],
            ):
                from wifey import main

                main()

            mock_main.assert_called_once_with(
                sort="pnl_pct:desc",
                telegram=True,
                hide_empty=True,
                compact=True,
                live=True,
            )

    def test_missing_subcommand_exits(self) -> None:
        """Missing subcommand causes SystemExit."""
        with patch("sys.argv", ["wifey.py"]):
            from wifey import main

            with pytest.raises(SystemExit):
                main()

    def test_missing_monitor_subcommand_exits(self) -> None:
        """'monitor' without price/position causes SystemExit."""
        with patch("sys.argv", ["wifey.py", "monitor"]):
            from wifey import main

            with pytest.raises(SystemExit):
                main()
