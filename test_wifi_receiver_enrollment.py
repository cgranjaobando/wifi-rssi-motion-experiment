import unittest

from wifi_receiver_enrollment import (
    ANDROID_RECEIVER_CLASSIFICATION,
    PRIVATE_MAC_CLASSIFICATION,
    WINDOWS_RECEIVER_CLASSIFICATION,
    create_enrollment_candidates,
    parse_arp_table,
)


class WifiReceiverEnrollmentTests(unittest.TestCase):
    def test_parse_arp_table_returns_dynamic_entries(self) -> None:
        arp_text = """
Interface: 192.168.40.3 --- 0x13
  Internet Address      Physical Address      Type
  192.168.40.1          58-72-c9-00-00-01     dynamic
  192.168.40.4          b4-45-06-00-00-03     dynamic
  192.168.40.255        ff-ff-ff-ff-ff-ff     static
"""

        entries = parse_arp_table(arp_text)

        self.assertEqual(3, len(entries))
        self.assertEqual("192.168.40.3", entries[0].interface_address)
        self.assertEqual("192.168.40.1", entries[0].ip_address)
        self.assertEqual("58-72-c9-00-00-01", entries[0].mac_address)

    def test_create_enrollment_candidates_classifies_known_devices(self) -> None:
        arp_entries = parse_arp_table(
            """
Interface: 192.168.40.3 --- 0x13
  Internet Address      Physical Address      Type
  192.168.40.1          58-72-c9-00-00-01     dynamic
  192.168.40.4          b4-45-06-00-00-03     dynamic
  192.168.40.5          46-e4-5c-00-00-04     dynamic
  192.168.40.9          a0-d7-f3-00-00-05     dynamic
"""
        )

        candidates = create_enrollment_candidates(
            arp_entries=arp_entries,
            server_url="http://192.168.40.3:8877",
            gateway_address="192.168.40.1",
            interval_seconds=1.0,
        )

        classifications = {
            candidate.ip_address: candidate.classification
            for candidate in candidates
        }
        self.assertEqual(WINDOWS_RECEIVER_CLASSIFICATION, classifications["192.168.40.4"])
        self.assertEqual(PRIVATE_MAC_CLASSIFICATION, classifications["192.168.40.5"])
        self.assertEqual(ANDROID_RECEIVER_CLASSIFICATION, classifications["192.168.40.9"])
        self.assertNotIn("192.168.40.1", classifications)

    def test_create_enrollment_candidates_generates_agent_command(self) -> None:
        arp_entries = parse_arp_table(
            """
Interface: 192.168.40.3 --- 0x13
  Internet Address      Physical Address      Type
  192.168.40.4          b4-45-06-00-00-03     dynamic
"""
        )

        candidates = create_enrollment_candidates(
            arp_entries=arp_entries,
            server_url="http://192.168.40.3:8877",
            gateway_address="192.168.40.1",
            interval_seconds=0.5,
        )

        self.assertEqual(1, len(candidates))
        self.assertEqual("receiver_192_168_40_4", candidates[0].receiver_id)
        self.assertIn("wifi_receiver_agent.py", candidates[0].command)
        self.assertIn("--interval 0.5", candidates[0].command)
        self.assertIn('"Dell 192.168.40.4"', candidates[0].command)


if __name__ == "__main__":
    unittest.main()
