"""Tests for wifey.py CLI argument parsing and dispatch."""

from unittest.mock import patch

import pytest


class TestCLIParsing:
    """Tests for CLI argument parsing in wifey.py."""

    def test_missing_subcommand_exits(self) -> None:
        """Missing subcommand causes SystemExit."""
        with patch("sys.argv", ["wifey.py"]):
            from wifey import main

            with pytest.raises(SystemExit):
                main()
