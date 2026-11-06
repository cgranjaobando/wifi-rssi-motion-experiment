import argparse
import ctypes
import csv
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from ctypes import wintypes


DEFAULT_GATEWAY_ADDRESS = "192.168.40.1"
DEFAULT_OUTPUT_PATH = Path("wifi_signal_samples.csv")
DEFAULT_SAMPLE_SECONDS = 60
DEFAULT_INTERVAL_SECONDS = 1.0
DEFAULT_BASELINE_SCAN_COUNT = 5
DEFAULT_SIGNAL_CHANGE_THRESHOLD = 3
DEFAULT_SCAN_REFRESH_SETTLE_SECONDS = 3.0
DEFAULT_BASELINE_ADAPTATION_RATE = 0.15
MINIMUM_CANDIDATE_SIGNAL_PERCENT = 0
MAXIMUM_CANDIDATE_SIGNAL_PERCENT = 100
MINIMUM_BASELINE_DEVIATION_PERCENT = 1.0
BASELINE_JITTER_FULL_PENALTY_PERCENT = 8.0
STRONG_MOVEMENT_CHANGED_SIGNAL_COUNT = 3
STRONG_MOVEMENT_SCORE = 8
SENSING_EVENT_SCHEMA_VERSION = "wifi-sensing-event-v1"
RSSI_MEASUREMENT_TIER = "rssi"
WIRELESS_CLIENT_VERSION = 2
WINDOWS_SUCCESS_CODE = 0
PING_TIMEOUT_MILLISECONDS = 250
NO_PACKET_LOSS_TEXT = "Lost = 0"
PING_LATENCY_PATTERN = re.compile(r"(?:Average =|Media =|Promedio =)\s*(\d+)ms", re.IGNORECASE)
WIRELESS_INTERFACE_IDENTIFIER_PATTERN = re.compile(r"^\s*GUID\s+:\s*([0-9a-f-]{36})\s*$", re.IGNORECASE | re.MULTILINE)
NETWORK_NAME_PATTERN = re.compile(r"^SSID\s+\d+\s+:\s*(.*)$")
HARDWARE_ADDRESS_PATTERN = re.compile(r"^\s*BSSID\s+\d+\s+:\s*([0-9a-f:]{17})\s*$", re.IGNORECASE)
SIGNAL_PATTERN = re.compile(r"^\s*Signal\s+:\s*(\d+)%")
BAND_PATTERN = re.compile(r"^\s*Band\s+:\s*(.*)$")
CHANNEL_PATTERN = re.compile(r"^\s*Channel\s+:\s*(\d+)")


@dataclass(frozen=True)
class AccessPointSample:
    collected_at: str
    network_name: str
    hardware_address: str
    signal_percent: int | None
    band: str
    channel: str
    gateway_latency_milliseconds: int | None
    gateway_reachable: bool


@dataclass(frozen=True)
class AccessPointBaseline:
    network_name: str
    hardware_address: str
    band: str
    channel: str
    signal_percent: float
    sample_count: int
    observed_ratio: float
    signal_standard_deviation: float
    signal_median_absolute_deviation: float
    reliability: float


@dataclass(frozen=True)
class SignalChange:
    network_name: str
    hardware_address: str
    band: str
    channel: str
    baseline_signal_percent: float
    current_signal_percent: int
    signal_delta: float
    baseline_signal_standard_deviation: float
    baseline_signal_median_absolute_deviation: float
    standardized_reaction: float
    radio_reliability: float


@dataclass(frozen=True)
class MovementAssessment:
    status: str
    score: float
    changed_signal_count: int
    watched_signal_count: int
    strongest_signal_delta: float


class WindowsIdentifierStructure(ctypes.Structure):
    _fields_ = [
        ("data_one", wintypes.DWORD),
        ("data_two", wintypes.WORD),
        ("data_three", wintypes.WORD),
        ("data_four", ctypes.c_ubyte * 8),
    ]


def normalize_hardware_address(value: str) -> str:
    return value.strip().lower().replace("-", ":")


def run_command(arguments: list[str]) -> str:
    completed = subprocess.run(
        arguments,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return f"{completed.stdout}\n{completed.stderr}".strip()


def collect_network_scan() -> str:
    return run_command(["netsh", "wlan", "show", "networks", "mode=bssid"])


def parse_wireless_interface_identifier(interface_text: str) -> UUID | None:
    match = WIRELESS_INTERFACE_IDENTIFIER_PATTERN.search(interface_text)
    if match is None:
        return None

    return UUID(match.group(1))


def create_windows_identifier_structure(identifier: UUID) -> WindowsIdentifierStructure:
    data_four = (ctypes.c_ubyte * 8).from_buffer_copy(identifier.bytes[8:])
    return WindowsIdentifierStructure(
        identifier.time_low,
        identifier.time_mid,
        identifier.time_hi_version,
        data_four,
    )


def trigger_windows_wireless_scan(interface_identifier: UUID) -> bool:
    if sys.platform != "win32":
        return False

    wireless_library = ctypes.WinDLL("wlanapi.dll")
    negotiated_version = wintypes.DWORD()
    client_handle = wintypes.HANDLE()

    open_result = wireless_library.WlanOpenHandle(
        WIRELESS_CLIENT_VERSION,
        None,
        ctypes.byref(negotiated_version),
        ctypes.byref(client_handle),
    )
    if open_result != WINDOWS_SUCCESS_CODE:
        return False

    try:
        windows_identifier = create_windows_identifier_structure(interface_identifier)
        scan_result = wireless_library.WlanScan(
            client_handle,
            ctypes.byref(windows_identifier),
            None,
            None,
            None,
        )
        return scan_result == WINDOWS_SUCCESS_CODE
    finally:
        wireless_library.WlanCloseHandle(client_handle, None)


def refresh_wireless_scan(scan_settle_seconds: float) -> bool:
    interface_text = run_command(["netsh", "wlan", "show", "interfaces"])
    interface_identifier = parse_wireless_interface_identifier(interface_text)
    if interface_identifier is None:
        return False

    was_scan_triggered = trigger_windows_wireless_scan(interface_identifier)
    if was_scan_triggered:
        time.sleep(scan_settle_seconds)

    return was_scan_triggered


def collect_fresh_network_scan(refresh_scan: bool, scan_settle_seconds: float) -> str:
    if refresh_scan:
        refresh_wireless_scan(scan_settle_seconds)

    return collect_network_scan()


def measure_gateway_latency(gateway_address: str) -> tuple[int | None, bool]:
    output = run_command(
        [
            "ping",
            "-n",
            "1",
            "-w",
            str(PING_TIMEOUT_MILLISECONDS),
            gateway_address,
        ]
    )
    latency_match = PING_LATENCY_PATTERN.search(output)
    latency = int(latency_match.group(1)) if latency_match else None
    return latency, NO_PACKET_LOSS_TEXT.lower() in output.lower()


def create_access_point_sample(
    network_name: str,
    hardware_address: str,
    signal_percent: int | None,
    band: str,
    channel: str,
    gateway_latency_milliseconds: int | None,
    gateway_reachable: bool,
) -> AccessPointSample | None:
    if not network_name or not hardware_address or signal_percent is None:
        return None

    return AccessPointSample(
        collected_at=datetime.now(timezone.utc).isoformat(),
        network_name=network_name,
        hardware_address=hardware_address,
        signal_percent=signal_percent,
        band=band,
        channel=channel,
        gateway_latency_milliseconds=gateway_latency_milliseconds,
        gateway_reachable=gateway_reachable,
    )


def parse_access_point_samples(
    scan_text: str,
    gateway_latency_milliseconds: int | None,
    gateway_reachable: bool,
) -> list[AccessPointSample]:
    samples: list[AccessPointSample] = []
    current_network_name = ""
    current_hardware_address = ""
    current_signal_percent: int | None = None
    current_band = ""
    current_channel = ""

    def finalize_current_access_point() -> None:
        sample = create_access_point_sample(
            current_network_name,
            current_hardware_address,
            current_signal_percent,
            current_band,
            current_channel,
            gateway_latency_milliseconds,
            gateway_reachable,
        )
        if sample is not None:
            samples.append(sample)

    for raw_line in scan_text.splitlines():
        network_name_match = NETWORK_NAME_PATTERN.match(raw_line)
        if network_name_match:
            finalize_current_access_point()
            current_network_name = network_name_match.group(1).strip()
            current_hardware_address = ""
            current_signal_percent = None
            current_band = ""
            current_channel = ""
            continue

        hardware_address_match = HARDWARE_ADDRESS_PATTERN.match(raw_line)
        if hardware_address_match:
            finalize_current_access_point()
            current_hardware_address = normalize_hardware_address(hardware_address_match.group(1))
            current_signal_percent = None
            current_band = ""
            current_channel = ""
            continue

        if not current_hardware_address:
            continue

        signal_match = SIGNAL_PATTERN.match(raw_line)
        if signal_match:
            current_signal_percent = int(signal_match.group(1))
            continue

        band_match = BAND_PATTERN.match(raw_line)
        if band_match:
            current_band = band_match.group(1).strip()
            continue

        channel_match = CHANNEL_PATTERN.match(raw_line)
        if channel_match:
            current_channel = channel_match.group(1).strip()

    finalize_current_access_point()
    return samples


def parse_access_point_sample(
    scan_text: str,
    target_network_name: str,
    target_hardware_address: str,
    gateway_latency_milliseconds: int | None,
    gateway_reachable: bool,
) -> AccessPointSample | None:
    target_address = normalize_hardware_address(target_hardware_address)

    for sample in parse_access_point_samples(
        scan_text,
        gateway_latency_milliseconds,
        gateway_reachable,
    ):
        has_target_name = sample.network_name.casefold() == target_network_name.casefold()
        has_target_address = sample.hardware_address == target_address
        if has_target_name and has_target_address:
            return sample

    return None


def get_access_point_key(sample: AccessPointSample) -> tuple[str, str]:
    return sample.network_name, sample.hardware_address


def select_candidate_samples(
    samples: list[AccessPointSample],
    excluded_network_names: set[str],
) -> list[AccessPointSample]:
    excluded_names = {network_name.casefold() for network_name in excluded_network_names}
    candidates: list[AccessPointSample] = []

    for sample in samples:
        if sample.network_name.casefold() in excluded_names:
            continue

        if sample.signal_percent is None:
            continue

        if sample.signal_percent < MINIMUM_CANDIDATE_SIGNAL_PERCENT:
            continue

        if sample.signal_percent > MAXIMUM_CANDIDATE_SIGNAL_PERCENT:
            continue

        candidates.append(sample)

    return candidates


def calculate_average_signal_percent(signal_values: list[int]) -> float:
    return sum(signal_values) / len(signal_values)


def calculate_signal_standard_deviation(signal_values: list[int], average_signal_percent: float) -> float:
    if len(signal_values) <= 1:
        return 0.0

    variance = sum((value - average_signal_percent) ** 2 for value in signal_values) / len(signal_values)
    return variance ** 0.5


def calculate_median_signal_percent(signal_values: list[float]) -> float:
    sorted_values = sorted(signal_values)
    midpoint_index = len(sorted_values) // 2
    if len(sorted_values) % 2 == 1:
        return sorted_values[midpoint_index]

    return (sorted_values[midpoint_index - 1] + sorted_values[midpoint_index]) / 2.0


def calculate_signal_median_absolute_deviation(signal_values: list[int]) -> float:
    if not signal_values:
        return 0.0

    median_signal_percent = calculate_median_signal_percent([float(value) for value in signal_values])
    deviations = [abs(value - median_signal_percent) for value in signal_values]
    return calculate_median_signal_percent(deviations)


def calculate_radio_reliability(
    observed_ratio: float,
    signal_standard_deviation: float,
    signal_median_absolute_deviation: float,
) -> float:
    jitter_percent = max(signal_standard_deviation, signal_median_absolute_deviation)
    jitter_penalty = min(1.0, jitter_percent / BASELINE_JITTER_FULL_PENALTY_PERCENT)
    return round(observed_ratio * (1.0 - jitter_penalty), 2)


def calculate_standardized_reaction(
    signal_delta: float,
    signal_median_absolute_deviation: float,
) -> float:
    baseline_deviation = max(signal_median_absolute_deviation, MINIMUM_BASELINE_DEVIATION_PERCENT)
    return round(abs(signal_delta) / baseline_deviation, 1)


def build_signal_baselines(
    sample_scans: list[list[AccessPointSample]],
    excluded_network_names: set[str],
) -> dict[tuple[str, str], AccessPointBaseline]:
    signal_values_by_key: dict[tuple[str, str], list[int]] = {}
    sample_by_key: dict[tuple[str, str], AccessPointSample] = {}

    for samples in sample_scans:
        for sample in select_candidate_samples(samples, excluded_network_names):
            access_point_key = get_access_point_key(sample)
            signal_values_by_key.setdefault(access_point_key, []).append(sample.signal_percent)
            sample_by_key.setdefault(access_point_key, sample)

    baselines: dict[tuple[str, str], AccessPointBaseline] = {}
    scan_count = len(sample_scans)
    for access_point_key, signal_values in signal_values_by_key.items():
        sample = sample_by_key[access_point_key]
        average_signal_percent = calculate_average_signal_percent(signal_values)
        signal_standard_deviation = calculate_signal_standard_deviation(signal_values, average_signal_percent)
        signal_median_absolute_deviation = calculate_signal_median_absolute_deviation(signal_values)
        observed_ratio = round(len(signal_values) / scan_count, 2) if scan_count else 0.0
        baselines[access_point_key] = AccessPointBaseline(
            network_name=sample.network_name,
            hardware_address=sample.hardware_address,
            band=sample.band,
            channel=sample.channel,
            signal_percent=round(average_signal_percent, 1),
            sample_count=len(signal_values),
            observed_ratio=observed_ratio,
            signal_standard_deviation=round(signal_standard_deviation, 1),
            signal_median_absolute_deviation=round(signal_median_absolute_deviation, 1),
            reliability=calculate_radio_reliability(
                observed_ratio,
                signal_standard_deviation,
                signal_median_absolute_deviation,
            ),
        )

    return baselines


def detect_signal_changes(
    samples: list[AccessPointSample],
    baselines: dict[tuple[str, str], AccessPointBaseline],
    signal_change_threshold: int,
) -> list[SignalChange]:
    changes: list[SignalChange] = []

    for sample in samples:
        if sample.signal_percent is None:
            continue

        baseline = baselines.get(get_access_point_key(sample))
        if baseline is None:
            continue

        signal_delta = round(sample.signal_percent - baseline.signal_percent, 1)
        if abs(signal_delta) < signal_change_threshold:
            continue

        standardized_reaction = calculate_standardized_reaction(
            signal_delta,
            baseline.signal_median_absolute_deviation,
        )
        changes.append(
            SignalChange(
                network_name=sample.network_name,
                hardware_address=sample.hardware_address,
                band=sample.band,
                channel=sample.channel,
                baseline_signal_percent=baseline.signal_percent,
                current_signal_percent=sample.signal_percent,
                signal_delta=signal_delta,
                baseline_signal_standard_deviation=baseline.signal_standard_deviation,
                baseline_signal_median_absolute_deviation=baseline.signal_median_absolute_deviation,
                standardized_reaction=standardized_reaction,
                radio_reliability=baseline.reliability,
            )
        )

    return sorted(changes, key=lambda change: abs(change.signal_delta), reverse=True)


def assess_movement(changes: list[SignalChange], watched_signal_count: int) -> MovementAssessment:
    score = round(sum(abs(change.signal_delta) for change in changes), 1)
    changed_signal_count = len(changes)
    strongest_signal_delta = round(
        max((abs(change.signal_delta) for change in changes), default=0.0),
        1,
    )

    if changed_signal_count >= STRONG_MOVEMENT_CHANGED_SIGNAL_COUNT or score >= STRONG_MOVEMENT_SCORE:
        status = "strong_movement"
    elif changed_signal_count > 0:
        status = "possible_movement"
    else:
        status = "stable"

    return MovementAssessment(
        status=status,
        score=score,
        changed_signal_count=changed_signal_count,
        watched_signal_count=watched_signal_count,
        strongest_signal_delta=strongest_signal_delta,
    )


def adapt_signal_baselines(
    baselines: dict[tuple[str, str], AccessPointBaseline],
    samples: list[AccessPointSample],
    excluded_network_names: set[str],
    adaptation_rate: float,
) -> dict[tuple[str, str], AccessPointBaseline]:
    if adaptation_rate <= 0:
        return baselines

    normalized_adaptation_rate = min(1.0, adaptation_rate)
    updated_baselines = dict(baselines)
    for sample in select_candidate_samples(samples, excluded_network_names):
        if sample.signal_percent is None:
            continue

        access_point_key = get_access_point_key(sample)
        baseline = updated_baselines.get(access_point_key)
        if baseline is None:
            continue

        adapted_signal = round(
            baseline.signal_percent * (1 - normalized_adaptation_rate)
            + sample.signal_percent * normalized_adaptation_rate,
            1,
        )
        updated_baselines[access_point_key] = replace(
            baseline,
            band=sample.band or baseline.band,
            channel=sample.channel or baseline.channel,
            signal_percent=adapted_signal,
        )

    return updated_baselines


def calculate_motion_confidence(assessment: MovementAssessment) -> float:
    if assessment.watched_signal_count <= 0:
        return 0.0

    score_confidence = min(1.0, assessment.score / STRONG_MOVEMENT_SCORE)
    count_confidence = min(1.0, assessment.changed_signal_count / STRONG_MOVEMENT_CHANGED_SIGNAL_COUNT)
    return round(max(score_confidence, count_confidence), 2)


def classify_spatial_context(assessment: MovementAssessment) -> str:
    if assessment.status == "strong_movement":
        return "environment_disturbance"
    if assessment.status == "possible_movement":
        return "possible_presence_change"
    return "baseline_stable"


def write_sample_header(output_path: Path) -> None:
    if output_path.exists():
        return

    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            [
                "collected_at",
                "network_name",
                "hardware_address",
                "signal_percent",
                "band",
                "channel",
                "gateway_latency_milliseconds",
                "gateway_reachable",
            ]
        )


def append_sample(output_path: Path, sample: AccessPointSample) -> None:
    with output_path.open("a", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            [
                sample.collected_at,
                sample.network_name,
                sample.hardware_address,
                sample.signal_percent,
                sample.band,
                sample.channel,
                sample.gateway_latency_milliseconds,
                sample.gateway_reachable,
            ]
        )


def create_motion_event_path(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.with_name(f"{path.stem}_motion_events.csv")


def create_sensing_event_path(output_path: str | Path) -> Path:
    path = Path(output_path)
    return path.with_name(f"{path.stem}_sensing_events.jsonl")


def write_motion_event_header(output_path: Path) -> None:
    if output_path.exists():
        return

    with output_path.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            [
                "collected_at",
                "status",
                "score",
                "changed_signal_count",
                "watched_signal_count",
                "strongest_signal_delta",
                "changes",
            ]
        )


def format_signal_changes(changes: list[SignalChange]) -> str:
    return "; ".join(
        f"{change.network_name}/{change.hardware_address} "
        f"{change.baseline_signal_percent}->{change.current_signal_percent} "
        f"delta={change.signal_delta:+.1f}"
        for change in changes
    )


def append_motion_event(
    output_path: Path,
    collected_at: str,
    assessment: MovementAssessment,
    changes: list[SignalChange],
) -> None:
    with output_path.open("a", newline="", encoding="utf-8") as output_file:
        writer = csv.writer(output_file)
        writer.writerow(
            [
                collected_at,
                assessment.status,
                assessment.score,
                assessment.changed_signal_count,
                assessment.watched_signal_count,
                assessment.strongest_signal_delta,
                format_signal_changes(changes),
            ]
        )


def create_sensing_event(
    collected_at: str,
    assessment: MovementAssessment,
    changes: list[SignalChange],
    signal_change_threshold: int,
    adaptive_baseline_enabled: bool,
    baseline_adaptation_rate: float,
    field_reconstruction: dict[str, object] | None = None,
) -> dict[str, object]:
    sensing_event = {
        "schema_version": SENSING_EVENT_SCHEMA_VERSION,
        "collected_at": collected_at,
        "measurement": {
            "tier": RSSI_MEASUREMENT_TIER,
            "source": "windows_netsh_wlan",
            "unit": "signal_percent",
        },
        "baseline": {
            "watched_signal_count": assessment.watched_signal_count,
            "signal_change_threshold": signal_change_threshold,
            "adaptive": adaptive_baseline_enabled,
            "adaptation_rate": baseline_adaptation_rate if adaptive_baseline_enabled else 0,
        },
        "derived": {
            "motion_status": assessment.status,
            "motion_score": assessment.score,
            "motion_confidence": calculate_motion_confidence(assessment),
            "changed_signal_count": assessment.changed_signal_count,
            "strongest_signal_delta": assessment.strongest_signal_delta,
            "spatial_context": classify_spatial_context(assessment),
            "localization_quality": "signal_path_disturbance_only",
        },
        "links": [
            {
                "network_name": change.network_name,
                "hardware_address": change.hardware_address,
                "band": change.band,
                "channel": change.channel,
                "baseline_signal_percent": change.baseline_signal_percent,
                "current_signal_percent": change.current_signal_percent,
                "signal_delta": change.signal_delta,
                "baseline_standard_deviation": change.baseline_signal_standard_deviation,
                "baseline_median_absolute_deviation": change.baseline_signal_median_absolute_deviation,
                "standardized_reaction": change.standardized_reaction,
                "radio_reliability": change.radio_reliability,
                "interpretation": "router_to_receiver_signal_path_changed",
            }
            for change in changes
        ],
        "privacy": {
            "contains_network_identifiers": True,
            "review_before_sharing": True,
        },
    }
    if field_reconstruction is not None:
        sensing_event["field_reconstruction"] = field_reconstruction
        sensing_event["derived"]["localization_quality"] = "rssi_tomographic_estimate"

    return sensing_event


def append_sensing_event(output_path: Path, sensing_event: dict[str, object]) -> None:
    with output_path.open("a", encoding="utf-8") as output_file:
        output_file.write(json.dumps(sensing_event, sort_keys=True, separators=(",", ":")))
        output_file.write("\n")


def run_monitor(
    target_network_name: str,
    target_hardware_address: str,
    gateway_address: str,
    output_path: Path,
    sample_seconds: int,
    interval_seconds: float,
) -> None:
    write_sample_header(output_path)
    started_at = time.monotonic()
    sample_count = 0

    while time.monotonic() - started_at < sample_seconds:
        gateway_latency, gateway_reachable = measure_gateway_latency(gateway_address)
        scan_text = collect_network_scan()
        sample = parse_access_point_sample(
            scan_text,
            target_network_name,
            target_hardware_address,
            gateway_latency,
            gateway_reachable,
        )

        if sample is None:
            print("target not found")
        else:
            sample_count += 1
            append_sample(output_path, sample)
            print(
                f"{sample.collected_at} signal={sample.signal_percent}% "
                f"band={sample.band} channel={sample.channel} "
                f"ping={sample.gateway_latency_milliseconds}ms "
                f"reachable={sample.gateway_reachable}"
            )

        time.sleep(interval_seconds)

    print(f"wrote {sample_count} samples to {output_path}")


def run_visible_network_monitor(
    gateway_address: str,
    output_path: Path,
    sample_seconds: int,
    interval_seconds: float,
) -> None:
    write_sample_header(output_path)
    started_at = time.monotonic()
    sample_count = 0

    while time.monotonic() - started_at < sample_seconds:
        gateway_latency, gateway_reachable = measure_gateway_latency(gateway_address)
        scan_text = collect_network_scan()
        samples = parse_access_point_samples(scan_text, gateway_latency, gateway_reachable)

        for sample in samples:
            append_sample(output_path, sample)
            sample_count += 1

        strongest_samples = sorted(
            samples,
            key=lambda item: item.signal_percent if item.signal_percent is not None else -1,
            reverse=True,
        )[:3]
        summary = ", ".join(
            f"{sample.network_name}/{sample.hardware_address}={sample.signal_percent}%"
            for sample in strongest_samples
        )
        print(f"visible={len(samples)} strongest=[{summary}]")
        time.sleep(interval_seconds)

    print(f"wrote {sample_count} samples to {output_path}")


def run_motion_detector(
    gateway_address: str,
    output_path: Path,
    sample_seconds: int,
    interval_seconds: float,
    excluded_network_names: set[str],
    baseline_scan_count: int,
    signal_change_threshold: int,
    refresh_scans: bool,
    scan_settle_seconds: float,
    adaptive_baseline_enabled: bool,
    baseline_adaptation_rate: float,
) -> None:
    write_sample_header(output_path)
    motion_event_path = create_motion_event_path(output_path)
    sensing_event_path = create_sensing_event_path(output_path)
    write_motion_event_header(motion_event_path)
    baseline_scans: list[list[AccessPointSample]] = []
    sample_count = 0
    event_count = 0

    while len(baseline_scans) < baseline_scan_count:
        gateway_latency, gateway_reachable = measure_gateway_latency(gateway_address)
        samples = parse_access_point_samples(
            collect_fresh_network_scan(refresh_scans, scan_settle_seconds),
            gateway_latency,
            gateway_reachable,
        )
        candidates = select_candidate_samples(samples, excluded_network_names)
        baseline_scans.append(samples)
        print(
            f"baseline_scan={len(baseline_scans)} "
            f"candidate_radios={len(candidates)} "
            f"scan_refresh={'on' if refresh_scans else 'off'}"
        )
        time.sleep(interval_seconds)

    baselines = build_signal_baselines(baseline_scans, excluded_network_names)
    if not baselines:
        print("no candidate routers found for motion detection")
        return

    baseline_summary = ", ".join(
        f"{baseline.network_name}/{baseline.hardware_address}={baseline.signal_percent}%"
        for baseline in sorted(baselines.values(), key=lambda item: item.signal_percent, reverse=True)
    )
    print(f"watching={len(baselines)} baselines=[{baseline_summary}]")

    started_at = time.monotonic()
    while time.monotonic() - started_at < sample_seconds:
        gateway_latency, gateway_reachable = measure_gateway_latency(gateway_address)
        samples = parse_access_point_samples(
            collect_fresh_network_scan(refresh_scans, scan_settle_seconds),
            gateway_latency,
            gateway_reachable,
        )
        candidates = select_candidate_samples(samples, excluded_network_names)
        changes = detect_signal_changes(candidates, baselines, signal_change_threshold)
        assessment = assess_movement(changes, watched_signal_count=len(baselines))
        collected_at = datetime.now(timezone.utc).isoformat()

        for sample in candidates:
            append_sample(output_path, sample)
            sample_count += 1

        append_motion_event(motion_event_path, collected_at, assessment, changes)
        append_sensing_event(
            sensing_event_path,
            create_sensing_event(
                collected_at,
                assessment,
                changes,
                signal_change_threshold,
                adaptive_baseline_enabled,
                baseline_adaptation_rate,
            ),
        )
        event_count += 1

        if adaptive_baseline_enabled and assessment.status == "stable":
            baselines = adapt_signal_baselines(
                baselines,
                candidates,
                excluded_network_names,
                baseline_adaptation_rate,
            )

        if changes:
            summary = ", ".join(
                f"{change.network_name}/{change.hardware_address}: "
                f"{change.baseline_signal_percent}%->{change.current_signal_percent}% "
                f"delta={change.signal_delta:+.1f}%"
                for change in changes
            )
            print(
                f"{assessment.status} score={assessment.score:.1f} "
                f"changed={assessment.changed_signal_count}/{assessment.watched_signal_count} "
                f"changes=[{summary}]"
            )
        else:
            print(
                f"{assessment.status} score={assessment.score:.1f} "
                f"candidate_radios={len(candidates)}"
            )

        time.sleep(interval_seconds)

    print(f"wrote {sample_count} candidate samples to {output_path}")
    print(f"wrote {event_count} motion events to {motion_event_path}")
    print(f"wrote {event_count} sensing events to {sensing_event_path}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Monitor a target WiFi access point signal.")
    parser.add_argument("--network-name")
    parser.add_argument("--hardware-address")
    parser.add_argument("--gateway-address", default=DEFAULT_GATEWAY_ADDRESS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--seconds", type=int, default=DEFAULT_SAMPLE_SECONDS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--all-visible", action="store_true")
    parser.add_argument("--detect-motion", action="store_true")
    parser.add_argument("--exclude-network-name", action="append", default=[])
    parser.add_argument("--baseline-scans", type=int, default=DEFAULT_BASELINE_SCAN_COUNT)
    parser.add_argument("--signal-change-threshold", type=int, default=DEFAULT_SIGNAL_CHANGE_THRESHOLD)
    parser.add_argument("--scan-settle-seconds", type=float, default=DEFAULT_SCAN_REFRESH_SETTLE_SECONDS)
    parser.add_argument("--adaptive-baseline", action="store_true")
    parser.add_argument("--baseline-adaptation-rate", type=float, default=DEFAULT_BASELINE_ADAPTATION_RATE)
    parser.add_argument("--skip-scan-refresh", action="store_true")
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    if arguments.detect_motion:
        run_motion_detector(
            gateway_address=arguments.gateway_address,
            output_path=arguments.output,
            sample_seconds=arguments.seconds,
            interval_seconds=arguments.interval,
            excluded_network_names=set(arguments.exclude_network_name),
            baseline_scan_count=arguments.baseline_scans,
            signal_change_threshold=arguments.signal_change_threshold,
            refresh_scans=not arguments.skip_scan_refresh,
            scan_settle_seconds=arguments.scan_settle_seconds,
            adaptive_baseline_enabled=arguments.adaptive_baseline,
            baseline_adaptation_rate=arguments.baseline_adaptation_rate,
        )
        return

    if arguments.all_visible:
        run_visible_network_monitor(
            gateway_address=arguments.gateway_address,
            output_path=arguments.output,
            sample_seconds=arguments.seconds,
            interval_seconds=arguments.interval,
        )
        return

    if not arguments.network_name or not arguments.hardware_address:
        raise SystemExit("--network-name and --hardware-address are required unless --all-visible is set")

    run_monitor(
        target_network_name=arguments.network_name,
        target_hardware_address=arguments.hardware_address,
        gateway_address=arguments.gateway_address,
        output_path=arguments.output,
        sample_seconds=arguments.seconds,
        interval_seconds=arguments.interval,
    )


if __name__ == "__main__":
    main()
