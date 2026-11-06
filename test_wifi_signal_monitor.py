import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from urllib.parse import parse_qs

from wifi_motion_live_server import (
    DEFAULT_EXCLUDED_NETWORK_NAMES,
    DEFAULT_OUTPUT_PATH,
    delete_dashboard_record_files,
    get_dashboard_record_paths,
    parse_excluded_network_names,
)

from wifi_signal_monitor import (
    AccessPointSample,
    adapt_signal_baselines,
    assess_movement,
    build_signal_baselines,
    create_sensing_event,
    create_sensing_event_path,
    create_motion_event_path,
    detect_signal_changes,
    normalize_hardware_address,
    parse_wireless_interface_identifier,
    parse_access_point_sample,
    parse_access_point_samples,
    select_candidate_samples,
)


class WifiSignalMonitorTests(unittest.TestCase):
    def create_sample(
        self,
        network_name: str,
        hardware_address: str,
        signal_percent: int,
    ) -> AccessPointSample:
        return AccessPointSample(
            collected_at="2026-05-29T00:00:00+00:00",
            network_name=network_name,
            hardware_address=hardware_address,
            signal_percent=signal_percent,
            band="2.4 GHz",
            channel="1",
            gateway_latency_milliseconds=1,
            gateway_reachable=True,
        )

    def test_parse_access_point_sample_returns_matching_router_signal(self) -> None:
        scan_text = """
SSID 1 : OtherNetwork
    Network type            : Infrastructure
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 55%
         Band               : 2.4 GHz
         Channel            : 1

SSID 2 : HomeNet
    Network type            : Infrastructure
    BSSID 1                 : 58:72:c9:00:00:01
         Signal             : 99%
         Radio type         : 802.11ax
         Band               : 2.4 GHz
         Channel            : 3
"""

        sample = parse_access_point_sample(
            scan_text=scan_text,
            target_network_name="HomeNet",
            target_hardware_address="58-72-c9-00-00-01",
            gateway_latency_milliseconds=1,
            gateway_reachable=True,
        )

        self.assertIsNotNone(sample)
        self.assertEqual("HomeNet", sample.network_name)
        self.assertEqual("58:72:c9:00:00:01", sample.hardware_address)
        self.assertEqual(99, sample.signal_percent)
        self.assertEqual("2.4 GHz", sample.band)
        self.assertEqual("3", sample.channel)
        self.assertEqual(1, sample.gateway_latency_milliseconds)
        self.assertTrue(sample.gateway_reachable)

    def test_parse_access_point_sample_returns_none_when_target_is_absent(self) -> None:
        scan_text = """
SSID 1 : OtherNetwork
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 55%
"""

        sample = parse_access_point_sample(
            scan_text=scan_text,
            target_network_name="HomeNet",
            target_hardware_address="58:72:c9:00:00:01",
            gateway_latency_milliseconds=None,
            gateway_reachable=False,
        )

        self.assertIsNone(sample)

    def test_parse_access_point_samples_returns_each_visible_router_radio(self) -> None:
        scan_text = """
SSID 1 : HomeNet
    BSSID 1                 : 58:72:c9:00:00:01
         Signal             : 99%
         Band               : 2.4 GHz
         Channel            : 3
    BSSID 2                 : 58:72:c9:00:00:02
         Signal             : 95%
         Band               : 5 GHz
         Channel            : 104
SSID 2 : OtherNetwork
    BSSID 1                 : aa:bb:cc:dd:ee:ff
         Signal             : 55%
         Band               : 2.4 GHz
         Channel            : 1
"""

        samples = parse_access_point_samples(
            scan_text=scan_text,
            gateway_latency_milliseconds=1,
            gateway_reachable=True,
        )

        self.assertEqual(3, len(samples))
        self.assertEqual(["HomeNet", "HomeNet", "OtherNetwork"], [sample.network_name for sample in samples])
        self.assertEqual([99, 95, 55], [sample.signal_percent for sample in samples])

    def test_normalize_hardware_address_accepts_windows_address_format(self) -> None:
        self.assertEqual(
            "58:72:c9:00:00:01",
            normalize_hardware_address("58-72-c9-00-00-01"),
        )

    def test_select_candidate_samples_includes_full_signal_range_except_excluded_ssids(self) -> None:
        samples = [
            self.create_sample("HomeNet", "58:72:c9:00:00:01", 99),
            self.create_sample("OtherStrong", "aa:bb:cc:dd:ee:01", 99),
            self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 84),
            self.create_sample("OtherWeak", "aa:bb:cc:dd:ee:03", 15),
        ]

        candidates = select_candidate_samples(samples, excluded_network_names={"HomeNet"})

        self.assertEqual(
            ["OtherStrong", "OtherUseful", "OtherWeak"],
            [sample.network_name for sample in candidates],
        )

    def test_build_signal_baselines_averages_candidate_router_samples(self) -> None:
        first_scan = [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 84)]
        second_scan = [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 86)]

        baselines = build_signal_baselines([first_scan, second_scan], excluded_network_names=set())

        baseline = baselines[("OtherUseful", "aa:bb:cc:dd:ee:02")]
        self.assertEqual(85.0, baseline.signal_percent)

    def test_build_signal_baselines_profiles_radio_reliability(self) -> None:
        first_scan = [
            self.create_sample("Stable", "aa:bb:cc:dd:ee:02", 50),
            self.create_sample("Intermittent", "aa:bb:cc:dd:ee:03", 50),
        ]
        second_scan = [
            self.create_sample("Stable", "aa:bb:cc:dd:ee:02", 50),
            self.create_sample("Intermittent", "aa:bb:cc:dd:ee:03", 52),
        ]
        third_scan = [self.create_sample("Stable", "aa:bb:cc:dd:ee:02", 50)]

        baselines = build_signal_baselines([first_scan, second_scan, third_scan], excluded_network_names=set())

        stable_baseline = baselines[("Stable", "aa:bb:cc:dd:ee:02")]
        intermittent_baseline = baselines[("Intermittent", "aa:bb:cc:dd:ee:03")]
        self.assertEqual(1.0, stable_baseline.observed_ratio)
        self.assertEqual(0.0, stable_baseline.signal_median_absolute_deviation)
        self.assertEqual(1.0, stable_baseline.reliability)
        self.assertEqual(0.67, intermittent_baseline.observed_ratio)
        self.assertEqual(1.0, intermittent_baseline.signal_median_absolute_deviation)
        self.assertLess(intermittent_baseline.reliability, stable_baseline.reliability)

    def test_detect_signal_changes_flags_candidate_router_movement(self) -> None:
        baselines = build_signal_baselines(
            [[self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 84)]],
            excluded_network_names=set(),
        )
        current_samples = [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 88)]

        changes = detect_signal_changes(current_samples, baselines, signal_change_threshold=3)

        self.assertEqual(1, len(changes))
        self.assertEqual("OtherUseful", changes[0].network_name)
        self.assertEqual(4.0, changes[0].signal_delta)

    def test_detect_signal_changes_includes_standardized_reaction(self) -> None:
        baselines = build_signal_baselines(
            [
                [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 50)],
                [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 52)],
                [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 51)],
            ],
            excluded_network_names=set(),
        )
        current_samples = [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 55)]

        changes = detect_signal_changes(current_samples, baselines, signal_change_threshold=2)

        self.assertEqual(1, len(changes))
        self.assertEqual(4.0, changes[0].signal_delta)
        self.assertEqual(4.0, changes[0].standardized_reaction)
        self.assertGreater(changes[0].radio_reliability, 0.8)

    def test_detect_signal_changes_ignores_small_candidate_router_changes(self) -> None:
        baselines = build_signal_baselines(
            [[self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 84)]],
            excluded_network_names=set(),
        )
        current_samples = [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 85)]

        changes = detect_signal_changes(current_samples, baselines, signal_change_threshold=3)

        self.assertEqual([], changes)

    def test_assess_movement_returns_stable_when_no_router_changes(self) -> None:
        assessment = assess_movement(changes=[], watched_signal_count=4)

        self.assertEqual("stable", assessment.status)
        self.assertEqual(0.0, assessment.score)
        self.assertEqual(0, assessment.changed_signal_count)

    def test_assess_movement_returns_possible_when_one_router_changes(self) -> None:
        baselines = build_signal_baselines(
            [[self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 84)]],
            excluded_network_names=set(),
        )
        changes = detect_signal_changes(
            [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 87)],
            baselines,
            signal_change_threshold=2,
        )

        assessment = assess_movement(changes=changes, watched_signal_count=4)

        self.assertEqual("possible_movement", assessment.status)
        self.assertEqual(3.0, assessment.score)
        self.assertEqual(1, assessment.changed_signal_count)

    def test_assess_movement_returns_strong_when_several_router_signals_change(self) -> None:
        baselines = build_signal_baselines(
            [
                [
                    self.create_sample("FirstUseful", "aa:bb:cc:dd:ee:02", 84),
                    self.create_sample("SecondUseful", "aa:bb:cc:dd:ee:03", 70),
                    self.create_sample("ThirdUseful", "aa:bb:cc:dd:ee:04", 55),
                ]
            ],
            excluded_network_names=set(),
        )
        current_samples = [
            self.create_sample("FirstUseful", "aa:bb:cc:dd:ee:02", 87),
            self.create_sample("SecondUseful", "aa:bb:cc:dd:ee:03", 74),
            self.create_sample("ThirdUseful", "aa:bb:cc:dd:ee:04", 52),
        ]
        changes = detect_signal_changes(current_samples, baselines, signal_change_threshold=2)

        assessment = assess_movement(changes=changes, watched_signal_count=3)

        self.assertEqual("strong_movement", assessment.status)
        self.assertEqual(10.0, assessment.score)
        self.assertEqual(3, assessment.changed_signal_count)

    def test_parse_wireless_interface_identifier_returns_interface_guid(self) -> None:
        interface_text = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    GUID                   : f944c990-ca80-41eb-a361-fe3ab0c5ed7f
"""

        identifier = parse_wireless_interface_identifier(interface_text)

        self.assertEqual("f944c990-ca80-41eb-a361-fe3ab0c5ed7f", str(identifier))

    def test_create_motion_event_path_keeps_output_folder(self) -> None:
        self.assertEqual(
            "samples_motion_events.csv",
            create_motion_event_path("samples.csv").name,
        )

    def test_create_sensing_event_path_keeps_output_folder(self) -> None:
        self.assertEqual(
            "samples_sensing_events.jsonl",
            create_sensing_event_path("samples.csv").name,
        )

    def test_create_sensing_event_returns_normalized_spatial_context(self) -> None:
        baselines = build_signal_baselines(
            [[self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 84)]],
            excluded_network_names=set(),
        )
        changes = detect_signal_changes(
            [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 88)],
            baselines,
            signal_change_threshold=3,
        )
        assessment = assess_movement(changes=changes, watched_signal_count=1)

        event = create_sensing_event(
            "2026-05-29T00:00:00+00:00",
            assessment,
            changes,
            signal_change_threshold=3,
            adaptive_baseline_enabled=True,
            baseline_adaptation_rate=0.15,
        )

        self.assertEqual("wifi-sensing-event-v1", event["schema_version"])
        self.assertEqual("rssi", event["measurement"]["tier"])
        self.assertEqual("possible_presence_change", event["derived"]["spatial_context"])
        self.assertEqual("signal_path_disturbance_only", event["derived"]["localization_quality"])
        self.assertEqual(1, len(event["links"]))
        self.assertTrue(event["privacy"]["contains_network_identifiers"])

    def test_create_sensing_event_can_include_field_reconstruction(self) -> None:
        assessment = assess_movement(changes=[], watched_signal_count=1)
        field_reconstruction = {
            "model": "rssi_fresnel_backprojection_v1",
            "peak": {"x_m": 2.0, "y_m": 1.5, "confidence": 0.5},
        }

        event = create_sensing_event(
            "2026-05-29T00:00:00+00:00",
            assessment,
            [],
            signal_change_threshold=3,
            adaptive_baseline_enabled=False,
            baseline_adaptation_rate=0.15,
            field_reconstruction=field_reconstruction,
        )

        self.assertEqual(field_reconstruction, event["field_reconstruction"])
        self.assertEqual("rssi_tomographic_estimate", event["derived"]["localization_quality"])

    def test_adapt_signal_baselines_moves_stable_baseline_toward_new_sample(self) -> None:
        baselines = build_signal_baselines(
            [[self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 80)]],
            excluded_network_names=set(),
        )

        updated = adapt_signal_baselines(
            baselines,
            [self.create_sample("OtherUseful", "aa:bb:cc:dd:ee:02", 90)],
            excluded_network_names=set(),
            adaptation_rate=0.2,
        )

        self.assertEqual(82.0, updated[("OtherUseful", "aa:bb:cc:dd:ee:02")].signal_percent)

    def test_parse_excluded_network_names_uses_default_when_query_is_missing(self) -> None:
        self.assertEqual(
            set(),
            parse_excluded_network_names({}),
        )
        self.assertEqual(set(), DEFAULT_EXCLUDED_NETWORK_NAMES)

    def test_parse_excluded_network_names_allows_empty_custom_filter(self) -> None:
        query = parse_qs("exclude=", keep_blank_values=True)

        self.assertEqual(set(), parse_excluded_network_names(query))

    def test_parse_excluded_network_names_trims_custom_filters(self) -> None:
        query = {
            "exclude": [" HomeNet ", "", "HomeNet_5G"],
        }

        self.assertEqual({"HomeNet", "HomeNet_5G"}, parse_excluded_network_names(query))

    def test_delete_dashboard_record_files_removes_only_live_dashboard_records(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            protected_file = directory / "movement_test.csv"
            protected_file.write_text("keep", encoding="utf-8")
            for record_path in get_dashboard_record_paths(directory):
                record_path.write_text("delete", encoding="utf-8")

            deleted_files, missing_files = delete_dashboard_record_files(directory)

            self.assertEqual(
                {
                    DEFAULT_OUTPUT_PATH.name,
                    "live_motion_samples_motion_events.csv",
                    "live_motion_samples_sensing_events.jsonl",
                },
                set(deleted_files),
            )
            self.assertEqual([], missing_files)
            self.assertTrue(protected_file.exists())
            for record_path in get_dashboard_record_paths(directory):
                self.assertFalse(record_path.exists())


if __name__ == "__main__":
    unittest.main()
