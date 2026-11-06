import unittest

from wifi_remote_receivers import RemoteReceiverRegistry, create_receiver_sample_packet


class WifiRemoteReceiverTests(unittest.TestCase):
    def test_create_receiver_sample_packet_normalizes_payload(self) -> None:
        packet = create_receiver_sample_packet(
            {
                "receiver": {
                    "id": "dell_receiver",
                    "label": "Dell receiver",
                },
                "samples": [
                    {
                        "collected_at": "2026-05-30T00:00:00+00:00",
                        "network_name": "HomeNet",
                        "hardware_address": "58-72-c9-00-00-01",
                        "signal_percent": 81,
                        "band": "2.4 GHz",
                        "channel": "10",
                        "gateway_latency_milliseconds": 2,
                        "gateway_reachable": True,
                    }
                ],
            }
        )

        self.assertEqual("dell_receiver", packet.receiver_id)
        self.assertEqual("Dell receiver", packet.receiver_label)
        self.assertEqual(1, len(packet.samples))
        self.assertEqual("58:72:c9:00:00:01", packet.samples[0].hardware_address)
        self.assertEqual(81, packet.samples[0].signal_percent)

    def test_receiver_registry_summarizes_latest_packet(self) -> None:
        packet = create_receiver_sample_packet(
            {
                "receiver": {
                    "id": "samsung_receiver",
                    "label": "Samsung receiver",
                },
                "samples": [
                    {
                        "collected_at": "2026-05-30T00:00:00+00:00",
                        "network_name": "Weak",
                        "hardware_address": "aa:bb:cc:dd:ee:01",
                        "signal_percent": 30,
                    },
                    {
                        "collected_at": "2026-05-30T00:00:00+00:00",
                        "network_name": "Strong",
                        "hardware_address": "aa:bb:cc:dd:ee:02",
                        "signal_percent": 88,
                    },
                ],
            }
        )
        registry = RemoteReceiverRegistry()

        registry.update(packet)
        summary = registry.summarize()

        self.assertEqual(1, len(summary["receivers"]))
        receiver_summary = summary["receivers"][0]
        self.assertEqual("samsung_receiver", receiver_summary["id"])
        self.assertEqual(2, receiver_summary["latest_sample_count"])
        self.assertEqual(2, receiver_summary["total_sample_count"])
        strongest_samples = receiver_summary["strongest_samples"]
        self.assertEqual("Strong", strongest_samples[0]["network_name"])
        self.assertEqual(88, strongest_samples[0]["signal_percent"])

    def test_create_receiver_sample_packet_rejects_missing_receiver_id(self) -> None:
        with self.assertRaises(ValueError):
            create_receiver_sample_packet(
                {
                    "receiver": {
                        "label": "Receiver without id",
                    },
                    "samples": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
