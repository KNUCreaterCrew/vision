from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
import unittest
from unittest.mock import Mock, patch

from mock_backend import make_server, validate_report
from zeroframe.state import Reason, SeatState, Status
from zeroframe.transport import (
    ApiClock, StatusPublisher, TransportConfig, build_report, load_transport_config, post_report,
)


KEY = "synthetic-test-key"
ORIGIN = datetime(2026, 11, 17, tzinfo=timezone.utc)


def states(status=Status.OCCUPIED, observed=100, since=100, reason=None):
    return {"A-01": SeatState(status, since, observed, reason)}


class TransportConfigurationTests(unittest.TestCase):
    def test_missing_default_settings_disable_network_and_explicit_missing_file_fails(self):
        with TemporaryDirectory() as directory, patch("zeroframe.transport.ROOT", Path(directory)), \
             patch.dict("os.environ", {}, clear=True):
            self.assertIsNone(load_transport_config())
            with self.assertRaises(FileNotFoundError):
                load_transport_config(Path(directory) / "missing.json")

    def test_json_and_environment_override_work_without_vscode(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"backend_url": "http://127.0.0.1:8765", "api_key": "placeholder"}))
            with patch.dict("os.environ", {"EDGE_NODE_SECRET_KEY": KEY}, clear=True):
                config = load_transport_config(path)
            self.assertEqual(config.api_key, KEY)
            self.assertNotIn(KEY, repr(config))
            self.assertEqual(config.timeout_seconds, 2)

    def test_invalid_urls_keys_and_timeout_are_rejected(self):
        for url in ("http://example.com", "http://192.168.0.14", "ftp://localhost", "https://",
                    "https://user:password@example.com", "https://example.com?a=1", "https://example.com/#x",
                    "https://example.com:99999", "https://example.com\n"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                TransportConfig(url, KEY)
        for key in ("", "bad\r\nheader", "has space", None):
            with self.subTest(key=key), self.assertRaises(ValueError):
                TransportConfig("https://example.com", key)
        for timeout in (0, True, float("nan"), 6):
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                TransportConfig("https://example.com", KEY, timeout)
        self.assertEqual(TransportConfig("http://[::1]:8765", KEY).api_key, KEY)

    def test_malformed_unknown_or_partial_settings_fail_without_disclosing_values(self):
        with TemporaryDirectory() as directory, patch.dict("os.environ", {}, clear=True):
            path = Path(directory) / "settings.json"
            for value in ("{", "[]", '{"api_key":"synthetic-test-key"}',
                          '{"api_key":"synthetic-test-key", "image":"no"}'):
                path.write_text(value)
                with self.subTest(value=value), self.assertRaises(ValueError) as raised:
                    load_transport_config(path)
                self.assertNotIn(KEY, str(raised.exception))


class ReportTests(unittest.TestCase):
    def test_vlm_empty_uses_existing_contract_source_until_expiry(self):
        snapshot = {"A-01": SeatState(Status.EMPTY, 100, 100, vlm_verified=True)}
        report = self.report(snapshot)
        validate_report(report)
        self.assertEqual(report["seats"][0]["decision_source"], "YOLO_VLM")
        self.assertEqual(self.report(snapshot, 130)["seats"][0]["decision_source"], "FALLBACK")

    def report(self, state=None, now=105):
        return build_report("IT4-101", state or states(), now=now, started_at=100,
                            inference_count=10, api_clock=ApiClock(100, ORIGIN))

    def test_contract_has_metadata_only_and_distinct_wall_and_observation_times(self):
        report = self.report()
        validate_report(report)
        self.assertEqual(report["timestamp"], "2026-11-17T00:00:05.000+00:00")
        self.assertEqual(report["inference_fps"], 2)
        self.assertEqual(report["saved_frames_count"], 0)
        self.assertEqual(report["seats"], [{"seat_id": "A-01", "status": "OCCUPIED", "confidence": None,
                                           "decision_source": "YOLO", "last_observed_at": "2026-11-17T00:00:00.000+00:00"}])
        self.assertEqual(set(report), {"room_id", "timestamp", "inference_fps", "saved_frames_count", "seats"})

    def test_initial_unknown_omits_unobserved_timestamp_and_never_becomes_empty(self):
        report = self.report(states(Status.UNKNOWN, None, reason=Reason.STALE_OBSERVATION))
        seat = report["seats"][0]
        self.assertNotIn("last_observed_at", seat)
        self.assertEqual((seat["status"], seat["decision_source"], seat["reason"]),
                         ("UNKNOWN", "FALLBACK", "STALE_OBSERVATION"))

    def test_worker_snapshot_expires_at_thirty_seconds_without_changing_observation_time(self):
        original = states()
        self.assertEqual(self.report(original, 129.999)["seats"][0]["status"], "OCCUPIED")
        report = self.report(original, 130)
        self.assertEqual(report["seats"][0]["status"], "UNKNOWN")
        self.assertEqual(report["seats"][0]["reason"], "STALE_OBSERVATION")
        self.assertEqual(report["seats"][0]["last_observed_at"], "2026-11-17T00:00:00.000+00:00")
        self.assertEqual(original["A-01"].status, Status.OCCUPIED)

    def test_future_observations_bad_counts_and_naive_clocks_are_rejected(self):
        with self.assertRaises(ValueError):
            self.report(states(observed=106), now=105)
        for count in (-1, True, float("nan")):
            with self.subTest(count=count), self.assertRaises(ValueError):
                build_report("R", states(), now=105, started_at=100, inference_count=count,
                             api_clock=ApiClock(100, ORIGIN))
        with self.assertRaises(ValueError):
            ApiClock(100, datetime(2026, 11, 17))


class PublisherTests(unittest.TestCase):
    def publisher(self, sender=None):
        sender = sender or Mock()
        return StatusPublisher(TransportConfig("http://127.0.0.1:8765", KEY), "IT4-101", states(),
                               started_at=100, clock=lambda: 100, api_clock=ApiClock(100, ORIGIN), sender=sender)

    def test_start_change_and_ten_second_heartbeat_use_latest_observation(self):
        publisher = self.publisher()
        self.assertTrue(publisher.send_due(100))
        publisher.update(states(observed=109), inference_count=18)
        self.assertFalse(publisher.send_due(109.999))
        self.assertTrue(publisher.send_due(110))
        self.assertEqual(publisher.sender.call_args.args[1]["seats"][0]["last_observed_at"],
                         "2026-11-17T00:00:09.000+00:00")
        publisher.update(states(Status.AWAY, observed=111, since=111), inference_count=20)
        self.assertTrue(publisher.send_due(111))
        self.assertEqual(publisher.sender.call_args.args[1]["seats"][0]["status"], "AWAY")

    def test_retry_discards_failed_history_and_does_not_log_credentials(self):
        sender = Mock(side_effect=[RuntimeError(KEY), None])
        publisher = self.publisher(sender)
        with patch("sys.stdout", new_callable=StringIO) as output:
            self.assertFalse(publisher.send_due(100))
            publisher.update(states(Status.AWAY, observed=100.5, since=100.5), inference_count=1)
            self.assertFalse(publisher.send_due(100.99))
            self.assertTrue(publisher.send_due(101))
        self.assertNotIn(KEY, output.getvalue())
        self.assertEqual(sender.call_args.args[1]["seats"][0]["status"], "AWAY")
        self.assertEqual((publisher.failure_count, publisher.sent_count), (1, 1))

    def test_retry_backoff_is_bounded_and_recovers_to_normal_schedule(self):
        sender = Mock(side_effect=OSError("offline"))
        publisher = self.publisher(sender)
        with patch("sys.stdout", new_callable=StringIO):
            for now, next_due in ((100, 101), (101, 103), (103, 107), (107, 115), (115, 125), (125, 135)):
                self.assertFalse(publisher.send_due(now))
                self.assertEqual(publisher._next_attempt_at, next_due)
            sender.side_effect = None
            self.assertTrue(publisher.send_due(135))
            self.assertFalse(publisher.send_due(144.99))
            self.assertTrue(publisher.send_due(145))

    def test_state_changed_during_inflight_send_is_reported_after_acknowledgement(self):
        publisher = self.publisher()
        publisher.sender.side_effect = lambda config, report: publisher.update(
            states(Status.AWAY), inference_count=1)
        self.assertTrue(publisher.send_due(100))
        publisher.sender.side_effect = None
        self.assertTrue(publisher.send_due(100))
        self.assertEqual(publisher.sender.call_args.args[1]["seats"][0]["status"], "AWAY")

    def test_invalid_snapshots_are_rejected_and_close_prevents_further_sends(self):
        publisher = self.publisher()
        for value in ({"A-02": states()["A-01"]}, {"A-01": object()}):
            with self.assertRaises(ValueError):
                publisher.update(value, inference_count=0)
        publisher.__exit__(None, None, None)
        self.assertFalse(publisher.send_due(100))
        publisher.sender.assert_not_called()

    def test_blocked_network_does_not_block_new_snapshots_and_only_latest_is_sent(self):
        entered, release, latest_sent = Event(), Event(), Event()
        reports = []

        def sender(config, report):
            reports.append(report)
            if len(reports) == 1:
                entered.set()
                if not release.wait(timeout=2):
                    raise RuntimeError("test synchronization failed")
            else:
                latest_sent.set()

        publisher = self.publisher(sender)
        with publisher:
            try:
                self.assertTrue(entered.wait(timeout=1))
                for _ in range(100):
                    publisher.update(states(Status.AWAY), inference_count=1)
                self.assertEqual(len(reports), 1)
            finally:
                release.set()
            self.assertTrue(latest_sent.wait(timeout=1))
        self.assertFalse(publisher._thread.is_alive())
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[-1]["seats"][0]["status"], "AWAY")


class HttpTransportTests(unittest.TestCase):
    def report(self):
        return build_report("IT4-101", states(), now=105, started_at=100,
                            inference_count=10, api_clock=ApiClock(100, ORIGIN))

    def test_real_loopback_post_validates_header_path_and_metadata(self):
        reports = []
        with make_server(port=0, api_key=KEY, on_report=reports.append) as server:
            thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                post_report(TransportConfig(f"http://127.0.0.1:{server.server_port}", KEY), self.report())
                with self.assertRaisesRegex(RuntimeError, "401"):
                    post_report(TransportConfig(f"http://127.0.0.1:{server.server_port}", "wrong"), self.report())
            finally:
                server.shutdown()
                thread.join(timeout=1)
        self.assertEqual(reports, [self.report()])

    def test_real_http_failure_recovers_with_latest_state_and_preserves_heartbeat_observation(self):
        reports, offline = [], [False]
        clock = [100]
        with make_server(port=0, api_key=KEY, on_report=reports.append) as server:
            original_post = server.RequestHandlerClass.do_POST

            def controlled_post(handler):
                if offline[0]:
                    handler.reply(503, {"ok": False})
                else:
                    original_post(handler)

            server.RequestHandlerClass.do_POST = controlled_post
            thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            config = TransportConfig(f"http://127.0.0.1:{server.server_port}", KEY)
            publisher = StatusPublisher(config, "IT4-101", states(), started_at=100,
                                        clock=lambda: clock[0], api_clock=ApiClock(100, ORIGIN))
            try:
                with patch("sys.stdout", new_callable=StringIO):
                    self.assertTrue(publisher.send_due(100))
                    offline[0], clock[0] = True, 101
                    publisher.update(states(Status.AWAY, 101, 101), inference_count=0)
                    self.assertFalse(publisher.send_due(101))
                    publisher.update(states(Status.UNKNOWN, 101, 102, Reason.OCCLUDED), inference_count=0)
                    offline[0], clock[0] = False, 102
                    self.assertTrue(publisher.send_due(102))
                    self.assertTrue(publisher.send_due(112))
            finally:
                publisher.__exit__(None, None, None)
                server.shutdown()
                thread.join(timeout=1)
        self.assertEqual([report["seats"][0]["status"] for report in reports],
                         ["OCCUPIED", "UNKNOWN", "UNKNOWN"])
        self.assertNotEqual(reports[1]["timestamp"], reports[2]["timestamp"])
        self.assertEqual(reports[1]["seats"][0]["last_observed_at"], reports[2]["seats"][0]["last_observed_at"])

    def test_redirects_and_bad_acknowledgements_fail_and_always_close_connection(self):
        acknowledgements = [(302, b""), (500, b""), (200, b"not-json"), (200, b'{"ok":false}'),
                            (200, b'{"ok":true,"updated_count":true,"server_time":"x"}'), (200, b"x" * 65537)]
        for status, data in acknowledgements:
            connection = Mock()
            connection.getresponse.return_value = Mock(status=status, read=Mock(return_value=data))
            with self.subTest(status=status, length=len(data)), \
                 patch("zeroframe.transport.http.client.HTTPSConnection", return_value=connection):
                with self.assertRaises(RuntimeError):
                    post_report(TransportConfig("https://example.com/base", KEY), self.report())
                connection.close.assert_called_once()
                self.assertEqual(connection.request.call_args.args[:2],
                                 ("POST", "/base/api/v1/rooms/IT4-101/seats/status"))

    def test_network_timeout_is_propagated_for_worker_retry(self):
        connection = Mock()
        connection.request.side_effect = TimeoutError("slow server")
        with patch("zeroframe.transport.http.client.HTTPSConnection", return_value=connection):
            with self.assertRaises(TimeoutError):
                post_report(TransportConfig("https://example.com", KEY), self.report())
        connection.close.assert_called_once()

    def test_receiver_rejects_image_fields_and_invalid_metadata(self):
        report = self.report()
        report["image"] = "forbidden"
        with self.assertRaises(ValueError):
            validate_report(report)
        report.pop("image")
        report["seats"][0]["confidence"] = float("nan")
        with self.assertRaises(ValueError):
            validate_report(report)

    def test_client_rejects_image_fields_before_opening_a_connection(self):
        report = self.report()
        report["seats"][0]["crop"] = "forbidden"
        with patch("zeroframe.transport.http.client.HTTPSConnection") as connection:
            with self.assertRaises(ValueError):
                post_report(TransportConfig("https://example.com", KEY), report)
            connection.assert_not_called()

    def test_encoded_room_identifiers_are_kept_consistent_in_real_requests(self):
        report = self.report()
        report["room_id"] = "한글 방/101"
        received = []
        with make_server(port=0, api_key=KEY, on_report=received.append) as server:
            thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
            thread.start()
            try:
                post_report(TransportConfig(f"http://127.0.0.1:{server.server_port}", KEY), report)
            finally:
                server.shutdown()
                thread.join(timeout=1)
        self.assertEqual(received[0]["room_id"], "한글 방/101")


if __name__ == "__main__":
    unittest.main()
