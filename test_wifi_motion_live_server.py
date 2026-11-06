import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from urllib import request

from wifi_motion_live_server import (
    DEFAULT_RECONSTRUCTION_METHOD,
    REGULARIZED_RECONSTRUCTION_METHOD,
    LiveMotionHandler,
    RemoteReceiverRegistry,
    create_field_reconstruction,
    get_remote_receiver_registry,
)
from wifi_field_simulator import create_demo_geometry
from wifi_signal_monitor import SignalChange


class LiveMotionServerTests(unittest.TestCase):
    def create_request(self, reconstruction_method: str | None = None):
        handler = object.__new__(LiveMotionHandler)
        query: dict[str, list[str]] = {}
        if reconstruction_method is not None:
            query["reconstruction"] = [reconstruction_method]

        return handler.create_live_motion_request(query)

    def test_live_request_defaults_to_backprojection_reconstruction(self) -> None:
        request = self.create_request()

        self.assertEqual(DEFAULT_RECONSTRUCTION_METHOD, request.reconstruction_method)

    def test_live_request_accepts_regularized_reconstruction(self) -> None:
        request = self.create_request(REGULARIZED_RECONSTRUCTION_METHOD)

        self.assertEqual(REGULARIZED_RECONSTRUCTION_METHOD, request.reconstruction_method)

    def test_live_request_uses_backprojection_for_unknown_reconstruction(self) -> None:
        request = self.create_request("unsupported")

        self.assertEqual(DEFAULT_RECONSTRUCTION_METHOD, request.reconstruction_method)

    def test_create_field_reconstruction_uses_selected_model(self) -> None:
        geometry = create_demo_geometry()
        change = SignalChange(
            network_name="AP_1",
            hardware_address="aa:bb:cc:dd:ee:01",
            band="2.4 GHz",
            channel="6",
            baseline_signal_percent=50.0,
            current_signal_percent=58,
            signal_delta=8.0,
            baseline_signal_standard_deviation=0.0,
            baseline_signal_median_absolute_deviation=0.0,
            standardized_reaction=8.0,
            radio_reliability=1.0,
        )

        backprojection = create_field_reconstruction([change], geometry, DEFAULT_RECONSTRUCTION_METHOD)
        regularized = create_field_reconstruction([change], geometry, REGULARIZED_RECONSTRUCTION_METHOD)

        self.assertIsNotNone(backprojection)
        self.assertIsNotNone(regularized)
        self.assertEqual("rssi_fresnel_backprojection_v1", backprojection["model"])
        self.assertEqual("rssi_tikhonov_dual_v1", regularized["model"])

    def test_get_remote_receiver_registry_reuses_server_registry(self) -> None:
        handler = SimpleNamespace(server=SimpleNamespace())

        first_registry = get_remote_receiver_registry(handler)
        second_registry = get_remote_receiver_registry(handler)

        self.assertIs(first_registry, second_registry)

    def test_remote_receiver_api_accepts_samples_and_reports_status(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            handler = lambda *args, **kwargs: LiveMotionHandler(
                *args,
                directory=temporary_directory,
                **kwargs,
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server.remote_receiver_registry = RemoteReceiverRegistry()
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            try:
                base_url = f"http://127.0.0.1:{server.server_address[1]}"
                payload = {
                    "receiver": {
                        "id": "dell_receiver",
                        "label": "Dell receiver",
                    },
                    "samples": [
                        {
                            "collected_at": "2026-05-30T00:00:00+00:00",
                            "network_name": "HomeNet",
                            "hardware_address": "58:72:c9:00:00:01",
                            "signal_percent": 90,
                        }
                    ],
                }
                post_response = post_json(f"{base_url}/api/receiver-samples", payload)
                receiver_response = get_json(f"{base_url}/api/receivers")
                status_response = get_json(f"{base_url}/api/status")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

        self.assertEqual("ok", post_response["status"])
        self.assertEqual("dell_receiver", post_response["receiver_id"])
        self.assertEqual(1, len(receiver_response["receivers"]))
        self.assertEqual("Dell receiver", receiver_response["receivers"][0]["label"])
        self.assertTrue(status_response["remote_receiver_api"])
        self.assertEqual(1, status_response["remote_receiver_count"])


def post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    http_request = request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def get_json(url: str) -> dict[str, object]:
    with request.urlopen(url, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
