import errno
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

        listener.setsockopt.assert_not_called()
        listener.bind.assert_called_once_with(("0.0.0.0", 54925))
        listener.settimeout.assert_called_once_with(30)
        register.assert_called_once_with(
            "scanner.example", "192.0.2.20", 54925, "Paperless", None
        )
        do_scan.assert_called_once_with(config)

    def test_image_event_runs_standalone_scan(self):
        config = self.daemon_config()
        listener = mock.Mock()
        listener.recvfrom.side_effect = [
            (
                b"TYPE=BR;BUTTON=SCAN;FUNC=IMAGE;REGID=1;SEQ=1;",
                ("192.0.2.10", 44127),
            ),
            KeyboardInterrupt,
        ]

        with (
            mock.patch.object(
                daemon_module.socket,
                "gethostbyname",
                return_value="192.0.2.10",
            ),
            mock.patch.object(
                daemon_module.socket,
                "socket",
                return_value=listener,
            ),
            mock.patch.object(daemon_module.signal, "signal"),
            mock.patch.object(daemon_module, "register_profiles"),
            mock.patch.object(daemon_module, "do_scan") as do_scan,
            mock.patch.object(daemon_module, "log"),
            mock.patch.object(daemon_module.time, "time", return_value=100),
        ):
            with self.assertRaises(KeyboardInterrupt):
                daemon_module.run_daemon(config)

        do_scan.assert_called_once_with(config)

    def test_daemon_scans_once_per_button_sequence(self):
        config = self.daemon_config()
        listener = mock.Mock()
        event_prefix = (
            b"\x02\x00|0TYPE=BR;BUTTON=SCAN;USER=Paperless;FUNC=FILE;"
            b"REGID=13990;SEQ="
        )
        listener.recvfrom.side_effect = [
            (event_prefix + b"5;", ("192.0.2.10", 44127)),
            (event_prefix + b"5;", ("192.0.2.10", 44127)),
            (event_prefix + b"6;", ("192.0.2.10", 44128)),
            KeyboardInterrupt,
        ]

        with (
            mock.patch.object(
                daemon_module.socket, "gethostbyname", return_value="192.0.2.10"
            ),
            mock.patch.object(daemon_module.socket, "socket", return_value=listener),
            mock.patch.object(daemon_module.signal, "signal"),
            mock.patch.object(daemon_module, "register_profiles"),
            mock.patch.object(daemon_module, "do_scan") as do_scan,
            mock.patch.object(daemon_module, "log") as log,
            mock.patch.object(daemon_module.time, "time", return_value=100),
            mock.patch.object(
                daemon_module.time,
                "monotonic",
                side_effect=(10, 60, 61, 62, 63),
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                daemon_module.run_daemon(config)

        self.assertEqual(do_scan.call_count, 2)
        do_scan.assert_has_calls([mock.call(config), mock.call(config)])
        self.assertIn(
            mock.call(
                "Ignored repeated scan event from ('192.0.2.10', 44127)",
                None,
            ),
            log.call_args_list,
        )

    def test_sequence_dedupe_is_anchored_after_long_scan(self):
        config = self.daemon_config()
        listener = mock.Mock()
        event = (
            b"TYPE=BR;BUTTON=SCAN;FUNC=FILE;REGID=13990;SEQ=5;"
        )
        listener.recvfrom.side_effect = [
            (event, ("192.0.2.10", 44127)),
            (event, ("192.0.2.10", 44127)),
            KeyboardInterrupt,
        ]

        with (
            mock.patch.object(
                daemon_module.socket,
                "gethostbyname",
                return_value="192.0.2.10",
            ),
            mock.patch.object(
                daemon_module.socket,
                "socket",
                return_value=listener,
            ),
            mock.patch.object(daemon_module.signal, "signal"),
            mock.patch.object(daemon_module, "register_profiles"),
            mock.patch.object(daemon_module, "do_scan") as do_scan,
            mock.patch.object(daemon_module, "log") as log,
            mock.patch.object(daemon_module.time, "time", return_value=100),
            mock.patch.object(
                daemon_module.time,
                "monotonic",
                side_effect=(10, 400, 401),
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                daemon_module.run_daemon(config)

        do_scan.assert_called_once_with(config)
        log.assert_any_call(
            "Ignored repeated scan event from ('192.0.2.10', 44127)",
            None,
        )

    def test_empty_sequence_retry_grace_allows_later_identical_press(self):
        config = self.daemon_config()
        listener = mock.Mock()
        event = b"TYPE=BR;BUTTON=SCAN;FUNC=IMAGE;REGID=13990;SEQ=;"
        listener.recvfrom.side_effect = [
            (event, ("192.0.2.10", 44127)),
            (event, ("192.0.2.10", 44127)),
            (event, ("192.0.2.10", 44127)),
            KeyboardInterrupt,
        ]
        with (
            mock.patch.object(
                daemon_module.socket,
                "gethostbyname",
                return_value="192.0.2.10",
            ),
            mock.patch.object(
                daemon_module.socket,
                "socket",
                return_value=listener,
            ),
            mock.patch.object(daemon_module.signal, "signal"),
            mock.patch.object(daemon_module, "register_profiles"),
            mock.patch.object(daemon_module, "do_scan") as do_scan,
            mock.patch.object(daemon_module, "log") as log,
            mock.patch.object(daemon_module.time, "time", return_value=100),
            mock.patch.object(
                daemon_module.time,
                "monotonic",
                side_effect=(10, 60, 61, 66, 67),
            ),
        ):
            with self.assertRaises(KeyboardInterrupt):
                daemon_module.run_daemon(config)

        self.assertEqual(do_scan.call_count, 2)
        do_scan.assert_has_calls([mock.call(config), mock.call(config)])
        log.assert_any_call(
            "Ignored repeated scan event from ('192.0.2.10', 44127)",
            None,
        )

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

    def test_daemon_rejects_an_existing_scan_button_listener(self):
        listener = mock.Mock()
        listener.bind.side_effect = OSError(
            errno.EADDRINUSE,
            "Address already in use",
        )

        with (
            mock.patch.object(
                daemon_module.socket, "gethostbyname", return_value="192.0.2.10"
            ),
            mock.patch.object(daemon_module.socket, "socket", return_value=listener),
            mock.patch.object(daemon_module, "register_profiles") as register,
            mock.patch.object(daemon_module, "log"),
            self.assertRaisesRegex(
                daemon_module.ScanListenerError,
                "UDP port 54925 is already in use; stop brscan-skey",
            ),
        ):
            daemon_module.run_daemon(self.daemon_config())

        listener.close.assert_called_once_with()
        listener.settimeout.assert_not_called()
        register.assert_not_called()
