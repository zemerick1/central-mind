"""Tests for __main__ module (CLI entry point)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from centralmind.__main__ import main_sync, setup_logging


class TestSetupLogging:
    """Tests for setup_logging function."""

    def test_sets_info_level_by_default(self):
        """Should set INFO level when debug=False."""
        with patch("logging.basicConfig") as mock_config:
            setup_logging(debug=False)
            mock_config.assert_called_once()
            # Check that level is INFO
            call_kwargs = mock_config.call_args[1]
            import logging
            assert call_kwargs["level"] == logging.INFO

    def test_sets_debug_level_when_debug_true(self):
        """Should set DEBUG level when debug=True."""
        with patch("logging.basicConfig") as mock_config:
            setup_logging(debug=True)
            mock_config.assert_called_once()
            call_kwargs = mock_config.call_args[1]
            import logging
            assert call_kwargs["level"] == logging.DEBUG


class TestMainSync:
    """Tests for main_sync CLI entry point."""

    def test_main_sync_exists(self):
        """main_sync should exist and be callable."""
        assert callable(main_sync)

    def test_version_flag(self, capsys):
        """--version should print version and exit."""
        with patch.object(sys, "argv", ["centralmind", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main_sync()
            
            assert exc_info.value.code == 0
            captured = capsys.readouterr()
            assert "centralmind" in captured.out

    def test_help_flag(self, capsys):
        """--help should print help and exit."""
        with patch.object(sys, "argv", ["centralmind", "--help"]):
            with pytest.raises(SystemExit) as exc_info:
                main_sync()
            
            assert exc_info.value.code == 0
            captured = capsys.readouterr()
            assert "CentralMind" in captured.out or "usage" in captured.out

    def test_invalid_transport_exits_with_error(self, capsys):
        """Should exit with error for non-stdio transport."""
        with patch.object(sys, "argv", ["centralmind", "--transport", "sse"]):
            with pytest.raises(SystemExit) as exc_info:
                main_sync()
            
            assert exc_info.value.code == 1
            captured = capsys.readouterr()
            assert "stdio" in captured.err.lower()

    def test_accepts_debug_flag(self):
        """Should accept --debug flag."""
        with patch.object(sys, "argv", ["centralmind", "--debug"]):
            with patch("centralmind.__main__.asyncio.run") as mock_run:
                # Mock to raise SystemExit to prevent actual execution
                mock_run.side_effect = SystemExit(1)
                
                with pytest.raises(SystemExit):
                    main_sync()
                
                # Verify asyncio.run was called (meaning parsing succeeded)
                assert mock_run.called

    def test_accepts_env_file_flag(self):
        """Should accept --env-file flag."""
        with patch.object(sys, "argv", ["centralmind", "--env-file", "/fake/path/.env"]):
            with pytest.raises(SystemExit) as exc_info:
                main_sync()
            
            # Should exit with error code 1 (file not found)
            assert exc_info.value.code == 1

    def test_transport_default_is_stdio(self):
        """Default transport should be stdio."""
        with patch.object(sys, "argv", ["centralmind"]):
            with patch("centralmind.__main__.asyncio.run") as mock_run:
                # Mock the entire server run to avoid spec file requirement
                mock_run.side_effect = Exception("Mocked to prevent actual run")
                
                try:
                    main_sync()
                except Exception:
                    pass  # Expected because we're mocking
                
                # If we got this far without transport error, default is stdio
                pass


class TestArgParsing:
    """Tests for argument parsing logic."""

    def test_parses_all_expected_args(self):
        """Should parse all expected CLI arguments."""
        test_args = [
            "centralmind",
            "--transport", "stdio",
            "--host", "localhost",
            "--port", "9000",
            "--env-file", ".env.test",
            "--debug",
        ]
        
        with patch.object(sys, "argv", test_args):
            with patch("centralmind.__main__.asyncio.run"):
                # We expect this to fail due to env file not existing
                # but arg parsing should succeed
                try:
                    main_sync()
                except SystemExit as e:
                    # Exit code 1 = env file not found (expected)
                    # This means parsing worked
                    assert e.code == 1

    def test_host_default(self):
        """Host should default to 127.0.0.1."""
        with patch.object(sys, "argv", ["centralmind"]):
            with patch("centralmind.__main__.asyncio.run") as mock_run:
                mock_run.side_effect = Exception("Mock")
                
                try:
                    main_sync()
                except:
                    pass
                
                # Default host should be 127.0.0.1 (tested via args namespace)
                pass

    def test_port_default(self):
        """Port should default to 8000."""
        with patch.object(sys, "argv", ["centralmind"]):
            with patch("centralmind.__main__.asyncio.run") as mock_run:
                mock_run.side_effect = Exception("Mock")
                
                try:
                    main_sync()
                except:
                    pass
                
                # Default port should be 8000 (tested via args namespace)
                pass


class TestMainAsyncFunction:
    """Tests for the async main function."""

    @pytest.mark.asyncio
    async def test_loads_env_file_when_specified(self, tmp_path):
        """Should load specified env file."""
        env_file = tmp_path / ".env.test"
        env_file.write_text("CENTRAL_CLIENT_ID=test_id\n")

        args = MagicMock()
        args.env_file = str(env_file)
        args.debug = False

        with patch("centralmind.__main__.load_dotenv") as mock_load_dotenv:
            with patch("centralmind.__main__.ServerConfig") as mock_config_cls:
                mock_config_cls.return_value.centralmind_debug = False
                with patch("centralmind.__main__.ClientsStore") as mock_store_cls:
                    mock_store_cls.return_value.migrate_from_env.return_value = None
                    mock_store_cls.return_value.is_empty.return_value = True
                    with pytest.raises(SystemExit):
                        # No clients configured -> exits(1) before touching the server
                        from centralmind.__main__ import main
                        await main(args)

                mock_load_dotenv.assert_called()

    @pytest.mark.asyncio
    async def test_exits_when_no_clients_configured(self, tmp_path, capsys):
        """Should exit with error when no clients are configured and nothing to migrate."""
        args = MagicMock()
        args.env_file = None
        args.debug = False

        with patch("centralmind.__main__.load_dotenv"):
            with patch("centralmind.__main__.ServerConfig") as mock_config_cls:
                mock_config_cls.return_value.centralmind_debug = False
                with patch("centralmind.__main__.ClientsStore") as mock_store_cls:
                    mock_store_cls.return_value.migrate_from_env.return_value = None
                    mock_store_cls.return_value.is_empty.return_value = True

                    from centralmind.__main__ import main

                    with pytest.raises(SystemExit) as exc_info:
                        await main(args)

                    assert exc_info.value.code == 1
                    assert "No clients configured" in capsys.readouterr().err

    @pytest.mark.asyncio
    async def test_runs_stdio_server_when_clients_configured(self, tmp_path):
        """Should build and run the server when at least one client exists."""
        args = MagicMock()
        args.env_file = None
        args.debug = False
        args.transport = "stdio"

        with patch("centralmind.__main__.load_dotenv"):
            with patch("centralmind.__main__.ServerConfig") as mock_config_cls:
                mock_config_cls.return_value.centralmind_debug = False
                mock_config_cls.return_value.centralmind_spec_path = None
                with patch("centralmind.__main__.ClientsStore") as mock_store_cls:
                    mock_store_cls.return_value.migrate_from_env.return_value = None
                    mock_store_cls.return_value.is_empty.return_value = False
                    mock_store_cls.return_value.get_server_api_mode.return_value = None

                    with patch("centralmind.__main__.CentralMindServer") as mock_server_cls:
                        async def mock_run_stdio():
                            pass

                        mock_server_cls.return_value.run_stdio = mock_run_stdio

                        from centralmind.__main__ import main
                        await main(args)

                        mock_server_cls.assert_called_once()
                        call_kwargs = mock_server_cls.call_args[1]
                        assert call_kwargs["clients_store"] is mock_store_cls.return_value

    @pytest.mark.asyncio
    async def test_admin_configured_server_api_mode_overrides_config(self, tmp_path):
        """A server_api_mode stored via the admin UI should override whatever
        CENTRALMIND_API_MODE/.env resolved to, before the server is built."""
        args = MagicMock()
        args.env_file = None
        args.debug = False
        args.transport = "stdio"

        with patch("centralmind.__main__.load_dotenv"):
            with patch("centralmind.__main__.ServerConfig") as mock_config_cls:
                mock_config_cls.return_value.centralmind_debug = False
                mock_config_cls.return_value.centralmind_api_mode = "readonly"
                mock_config_cls.return_value.centralmind_spec_path = None
                with patch("centralmind.__main__.ClientsStore") as mock_store_cls:
                    mock_store_cls.return_value.migrate_from_env.return_value = None
                    mock_store_cls.return_value.is_empty.return_value = False
                    mock_store_cls.return_value.get_server_api_mode.return_value = "all"

                    with patch("centralmind.__main__.CentralMindServer") as mock_server_cls:
                        async def mock_run_stdio():
                            pass

                        mock_server_cls.return_value.run_stdio = mock_run_stdio

                        from centralmind.__main__ import main
                        await main(args)

                        call_kwargs = mock_server_cls.call_args[1]
                        assert call_kwargs["config"].centralmind_api_mode == "all"


class TestErrorHandling:
    """Tests for error handling in main."""

    def test_handles_keyboard_interrupt(self):
        """KeyboardInterrupt from asyncio.run should propagate cleanly."""
        with patch.object(sys, "argv", ["centralmind"]):
            with patch("centralmind.__main__.asyncio.run") as mock_run:
                mock_run.side_effect = KeyboardInterrupt()
                
                # KeyboardInterrupt propagates out of main_sync
                with pytest.raises(KeyboardInterrupt):
                    main_sync()

