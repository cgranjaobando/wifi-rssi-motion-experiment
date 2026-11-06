import argparse
import json
import time
from urllib import request

from wifi_remote_receivers import sample_to_dict
from wifi_signal_monitor import (
    DEFAULT_GATEWAY_ADDRESS,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_SCAN_REFRESH_SETTLE_SECONDS,
    collect_fresh_network_scan,
    measure_gateway_latency,
    parse_access_point_samples,
    select_candidate_samples,
)


DEFAULT_SERVER_URL = "http://192.168.40.3:8877"
DEFAULT_RECEIVER_ID = "remote_receiver"
DEFAULT_RECEIVER_LABEL = "Remote WiFi receiver"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5
RECEIVER_SAMPLE_SCHEMA_VERSION = "wifi-receiver-samples-v1"
RECEIVER_SAMPLE_SOURCE = "windows_netsh_wlan"


def collect_receiver_samples(
    gateway_address: str,
    refresh_scans: bool,
    scan_settle_seconds: float,
    excluded_network_names: set[str],
) -> list[dict[str, object]]:
    gateway_latency, gateway_reachable = measure_gateway_latency(gateway_address)
    scan_text = collect_fresh_network_scan(refresh_scans, scan_settle_seconds)
    samples = parse_access_point_samples(scan_text, gateway_latency, gateway_reachable)
    candidates = select_candidate_samples(samples, excluded_network_names)
    return [sample_to_dict(sample) for sample in candidates]


def send_receiver_samples(
    server_url: str,
    receiver_id: str,
    receiver_label: str,
    samples: list[dict[str, object]],
) -> dict[str, object]:
    payload = {
        "schema_version": RECEIVER_SAMPLE_SCHEMA_VERSION,
        "receiver": {
            "id": receiver_id,
            "label": receiver_label,
            "source": RECEIVER_SAMPLE_SOURCE,
        },
        "samples": samples,
        "privacy": {
            "packet_payload_inspection": False,
            "contains_network_identifiers": True,
            "consented_receiver": True,
        },
    }
    content = json.dumps(payload).encode("utf-8")
    receiver_request = request.Request(
        f"{server_url.rstrip('/')}/api/receiver-samples",
        data=content,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(receiver_request, timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def run_receiver_agent(arguments: argparse.Namespace) -> None:
    started_at = time.monotonic()
    sent_packets = 0
    excluded_network_names = set(arguments.exclude_network_name)

    while should_keep_running(started_at, arguments.seconds):
        samples = collect_receiver_samples(
            gateway_address=arguments.gateway_address,
            refresh_scans=not arguments.skip_scan_refresh,
            scan_settle_seconds=arguments.scan_settle_seconds,
            excluded_network_names=excluded_network_names,
        )
        response = send_receiver_samples(
            server_url=arguments.server_url,
            receiver_id=arguments.receiver_id,
            receiver_label=arguments.receiver_label,
            samples=samples,
        )
        sent_packets += 1
        print(
            f"sent packet={sent_packets} receiver={arguments.receiver_id} "
            f"samples={len(samples)} server_status={response.get('status')}"
        )
        time.sleep(arguments.interval)


def should_keep_running(started_at: float, seconds: int) -> bool:
    if seconds <= 0:
        return True

    return time.monotonic() - started_at < seconds


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send local WiFi RSSI scans to the central motion server.")
    parser.add_argument("--server-url", default=DEFAULT_SERVER_URL)
    parser.add_argument("--receiver-id", default=DEFAULT_RECEIVER_ID)
    parser.add_argument("--receiver-label", default=DEFAULT_RECEIVER_LABEL)
    parser.add_argument("--gateway-address", default=DEFAULT_GATEWAY_ADDRESS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    parser.add_argument("--seconds", type=int, default=0)
    parser.add_argument("--scan-settle-seconds", type=float, default=DEFAULT_SCAN_REFRESH_SETTLE_SECONDS)
    parser.add_argument("--exclude-network-name", action="append", default=[])
    parser.add_argument("--skip-scan-refresh", action="store_true")
    return parser.parse_args()


def main() -> None:
    run_receiver_agent(parse_arguments())


if __name__ == "__main__":
    main()
