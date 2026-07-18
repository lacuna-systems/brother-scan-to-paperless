import socket
import unittest
from unittest import mock

import brother_scan_to_paperless.daemon as daemon_module


class DaemonLoopTests(unittest.TestCase):
    def daemon_config(self):
        return {
            "printer_ip": "scanner.example",
            "host_ip": "192.0.2.20",
            "consume_dir": "/consume",
            "scanner_device": "brother5:net1;dev0",
            "resolution": 300,
            "format": "tiff",
            "listen_port": 54925,
            "display_name": "Paperless",
            "register_interval": 300,
            "log_file": None,
        }

    def test_daemon_registers_listens_and_scans_matching_event(self):
        config = self.daemon_config()
        listener = mock.Mock()
        listener.recvfrom.side_effect = [
            (b"TYPE=BR;BUTTON=SCAN;FUNC=FILE;", ("192.0.2.10", 12345)),
            KeyboardInterrupt,
        ]

        with (
            mock.patch.object(
                daemon_module.socket, "gethostbyname", return_value="192.0.2.10"
            ),
            mock.patch.object(daemon_module.socket, "socket", return_value=listener),
            mock.patch.object(daemon_module.signal, "signal"),
            mock.patch.object(daemon_module, "register_profiles") as register,
            mock.patch.object(daemon_module, "do_scan") as do_scan,
            mock.patch.object(daemon_module, "log"),
            mock.patch.object(daemon_module.time, "time", return_value=100),
        ):
            with self.assertRaises(KeyboardInterrupt):
                daemon_module.run_daemon(config)

        listener.setsockopt.assert_called_once_with(
            socket.SOL_SOCKET, socket.SO_REUSEADDR, 1
        )
        listener.bind.assert_called_once_with(("0.0.0.0", 54925))
        listener.settimeout.assert_called_once_with(30)
        register.assert_called_once_with(
            "scanner.example", "192.0.2.20", 54925, "Paperless", None
        )
        do_scan.assert_called_once_with(config)

    def test_daemon_reregisters_after_interval(self):
        config = self.daemon_config()
        listener = mock.Mock()
        listener.recvfrom.side_effect = KeyboardInterrupt

        with (
            mock.patch.object(
                daemon_module.socket, "gethostbyname", return_value="192.0.2.10"
            ),
            mock.patch.object(daemon_module.socket, "socket", return_value=listener),
            mock.patch.object(daemon_module.signal, "signal"),
            mock.patch.object(daemon_module, "register_profiles") as register,
            mock.patch.object(daemon_module, "log"),
            mock.patch.object(daemon_module.time, "time", side_effect=(0, 301, 301)),
        ):
            with self.assertRaises(KeyboardInterrupt):
                daemon_module.run_daemon(config)

        self.assertEqual(register.call_count, 2)

    def test_daemon_rejects_unresolvable_printer_host(self):
        with (
            mock.patch.object(
                daemon_module.socket,
                "gethostbyname",
                side_effect=socket.gaierror("not found"),
            ),
            mock.patch.object(daemon_module, "log"),
        ):
            with self.assertRaisesRegex(ValueError, "cannot resolve printer_ip"):
                daemon_module.run_daemon(self.daemon_config())
