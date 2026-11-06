import argparse
import json
import time
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wifi_field_model import (
    DEFAULT_REGULARIZATION_FACTOR,
    reconstruct_from_signal_changes,
    reconstruct_regularized_from_signal_changes,
)
from wifi_geometry import DEFAULT_GEOMETRY_PATH, SensorGeometry, load_room_geometry_if_available
from wifi_remote_receivers import RemoteReceiverRegistry, create_receiver_sample_packet
from wifi_signal_monitor import (
    DEFAULT_BASELINE_SCAN_COUNT,
    DEFAULT_BASELINE_ADAPTATION_RATE,
    DEFAULT_GATEWAY_ADDRESS,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_SCAN_REFRESH_SETTLE_SECONDS,
    DEFAULT_SIGNAL_CHANGE_THRESHOLD,
    AccessPointBaseline,
    AccessPointSample,
    MovementAssessment,
    SignalChange,
    append_motion_event,
    append_sensing_event,
    append_sample,
    assess_movement,
    adapt_signal_baselines,
    build_signal_baselines,
    collect_fresh_network_scan,
    create_motion_event_path,
    create_sensing_event,
    create_sensing_event_path,
    detect_signal_changes,
    format_signal_changes,
    measure_gateway_latency,
    parse_access_point_samples,
    select_candidate_samples,
    write_motion_event_header,
    write_sample_header,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8877
DEFAULT_OUTPUT_PATH = Path("live_motion_samples.csv")
DEFAULT_EXCLUDED_NETWORK_NAMES: set[str] = set()
DEFAULT_RECONSTRUCTION_METHOD = "backprojection"
REGULARIZED_RECONSTRUCTION_METHOD = "regularized"
SUPPORTED_RECONSTRUCTION_METHODS = {
    DEFAULT_RECONSTRUCTION_METHOD,
    REGULARIZED_RECONSTRUCTION_METHOD,
}


class LiveMotionRequest:
    def __init__(
        self,
        gateway_address: str,
        output_path: Path,
        baseline_scan_count: int,
        signal_change_threshold: int,
        interval_seconds: float,
        scan_settle_seconds: float,
        excluded_network_names: set[str],
        refresh_scans: bool,
        adaptive_baseline_enabled: bool,
        baseline_adaptation_rate: float,
        reconstruction_method: str,
    ) -> None:
        self.gateway_address = gateway_address
        self.output_path = output_path
        self.baseline_scan_count = baseline_scan_count
        self.signal_change_threshold = signal_change_threshold
        self.interval_seconds = interval_seconds
        self.scan_settle_seconds = scan_settle_seconds
        self.excluded_network_names = excluded_network_names
        self.refresh_scans = refresh_scans
        self.adaptive_baseline_enabled = adaptive_baseline_enabled
        self.baseline_adaptation_rate = baseline_adaptation_rate
        self.reconstruction_method = reconstruction_method


class LiveMotionHandler(SimpleHTTPRequestHandler):
    server_version = "WiFiMotionLive/1.0"

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionAbortedError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/api/status":
            receiver_summary = get_remote_receiver_registry(self).summarize()
            self.send_json_response(
                {
                    "service": "wifi-motion-live",
                    "live_api": True,
                    "remote_receiver_api": True,
                    "remote_receiver_count": len(receiver_summary["receivers"]),
                }
            )
            return

        if parsed_url.path == "/api/geometry":
            geometry = load_geometry_for_directory(Path(self.directory))
            self.send_json_response(create_geometry_response(geometry))
            return

        if parsed_url.path == "/api/receivers":
            self.send_json_response(get_remote_receiver_registry(self).summarize())
            return

        if parsed_url.path == "/api/live":
            self.stream_live_motion(parse_qs(parsed_url.query, keep_blank_values=True))
            return

        if parsed_url.path == "/":
            self.path = "/wifi_motion_dashboard.html"

        super().do_GET()

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/api/receiver-samples":
            self.store_remote_receiver_samples()
            return

        self.send_error(HTTPStatus.NOT_FOUND, "API endpoint not found")

    def do_DELETE(self) -> None:
        parsed_url = urlparse(self.path)
        if parsed_url.path == "/api/records":
            deleted_files, missing_files = delete_dashboard_record_files(Path(self.directory))
            self.send_json_response(
                {
                    "deleted": deleted_files,
                    "missing": missing_files,
                }
            )
            return

        self.send_error(HTTPStatus.NOT_FOUND, "API endpoint not found")

    def send_json_response(self, payload: dict[str, object]) -> None:
        content = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def send_json_error(self, status: HTTPStatus, message: str) -> None:
        content = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def store_remote_receiver_samples(self) -> None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            packet = create_receiver_sample_packet(payload)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as error:
            self.send_json_error(HTTPStatus.BAD_REQUEST, str(error))
            return

        get_remote_receiver_registry(self).update(packet)
        self.send_json_response(
            {
                "status": "ok",
                "receiver_id": packet.receiver_id,
                "sample_count": len(packet.samples),
            }
        )

    def stream_live_motion(self, query: dict[str, list[str]]) -> None:
        request = self.create_live_motion_request(query)
        write_sample_header(request.output_path)
        motion_event_path = create_motion_event_path(request.output_path)
        sensing_event_path = create_sensing_event_path(request.output_path)
        write_motion_event_header(motion_event_path)

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        baseline_scans: list[list[AccessPointSample]] = []
        geometry = load_geometry_for_directory(Path(self.directory))
        try:
            while len(baseline_scans) < request.baseline_scan_count:
                samples = self.collect_samples(request)
                candidates = select_candidate_samples(samples, request.excluded_network_names)
                baseline_scans.append(samples)
                self.send_event(
                    "baseline",
                    {
                        "scan": len(baseline_scans),
                        "total": request.baseline_scan_count,
                        "candidate_count": len(candidates),
                        "refresh_scans": request.refresh_scans,
                    },
                )
                time.sleep(request.interval_seconds)

            baselines = build_signal_baselines(baseline_scans, request.excluded_network_names)
            self.send_event(
                "ready",
                {
                    "baselines": [baseline_to_dict(item) for item in baselines.values()],
                    "geometry_config_loaded": geometry is not None,
                    "field_reconstruction_enabled": geometry is not None,
                },
            )

            while True:
                samples = self.collect_samples(request)
                candidates = select_candidate_samples(samples, request.excluded_network_names)
                changes = detect_signal_changes(
                    candidates,
                    baselines,
                    request.signal_change_threshold,
                )
                assessment = assess_movement(changes, watched_signal_count=len(baselines))
                collected_at = current_utc_timestamp()

                for sample in candidates:
                    append_sample(request.output_path, sample)

                append_motion_event(motion_event_path, collected_at, assessment, changes)
                field_reconstruction = create_field_reconstruction(
                    changes, geometry, request.reconstruction_method
                )
                sensing_event = create_sensing_event(
                    collected_at,
                    assessment,
                    changes,
                    request.signal_change_threshold,
                    request.adaptive_baseline_enabled,
                    request.baseline_adaptation_rate,
                    field_reconstruction=field_reconstruction,
                )
                append_sensing_event(sensing_event_path, sensing_event)
                self.send_event(
                    "snapshot",
                    {
                        "collected_at": collected_at,
                        "assessment": assessment_to_dict(assessment),
                        "samples": [sample_to_dict(item) for item in candidates],
                        "changes": [change_to_dict(item) for item in changes],
                        "changes_text": format_signal_changes(changes),
                        "sensing_event": sensing_event,
                    },
                )
                if request.adaptive_baseline_enabled and assessment.status == "stable":
                    baselines = adapt_signal_baselines(
                        baselines,
                        candidates,
                        request.excluded_network_names,
                        request.baseline_adaptation_rate,
                    )
                time.sleep(request.interval_seconds)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def collect_samples(self, request: LiveMotionRequest) -> list[AccessPointSample]:
        gateway_latency, gateway_reachable = measure_gateway_latency(request.gateway_address)
        scan_text = collect_fresh_network_scan(request.refresh_scans, request.scan_settle_seconds)
        return parse_access_point_samples(scan_text, gateway_latency, gateway_reachable)

    def create_live_motion_request(self, query: dict[str, list[str]]) -> LiveMotionRequest:
        return LiveMotionRequest(
            gateway_address=get_query_value(query, "gateway", DEFAULT_GATEWAY_ADDRESS),
            output_path=Path(get_query_value(query, "output", str(DEFAULT_OUTPUT_PATH))),
            baseline_scan_count=get_positive_int(query, "baseline_scans", DEFAULT_BASELINE_SCAN_COUNT),
            signal_change_threshold=get_positive_int(query, "threshold", DEFAULT_SIGNAL_CHANGE_THRESHOLD),
            interval_seconds=get_positive_float(query, "interval", DEFAULT_INTERVAL_SECONDS),
            scan_settle_seconds=get_positive_float(query, "settle", DEFAULT_SCAN_REFRESH_SETTLE_SECONDS),
            excluded_network_names=parse_excluded_network_names(query),
            refresh_scans=get_query_value(query, "refresh", "1") != "0",
            adaptive_baseline_enabled=get_query_value(query, "adaptive", "1") != "0",
            baseline_adaptation_rate=get_positive_float(query, "adaptation_rate", DEFAULT_BASELINE_ADAPTATION_RATE),
            reconstruction_method=parse_reconstruction_method(query),
        )

    def send_event(self, event_name: str, payload: dict[str, object]) -> None:
        self.wfile.write(f"event: {event_name}\n".encode("utf-8"))
        self.wfile.write(f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8"))
        self.wfile.flush()

    def log_message(self, format: str, *args: object) -> None:
        return


def current_utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def get_remote_receiver_registry(handler: LiveMotionHandler) -> RemoteReceiverRegistry:
    registry = getattr(handler.server, "remote_receiver_registry", None)
    if isinstance(registry, RemoteReceiverRegistry):
        return registry

    registry = RemoteReceiverRegistry()
    setattr(handler.server, "remote_receiver_registry", registry)
    return registry


def load_geometry_for_directory(directory: Path) -> SensorGeometry | None:
    return load_room_geometry_if_available(directory / DEFAULT_GEOMETRY_PATH)


def create_field_reconstruction(
    changes: list[SignalChange],
    geometry: SensorGeometry | None,
    reconstruction_method: str = DEFAULT_RECONSTRUCTION_METHOD,
) -> dict[str, object] | None:
    if geometry is None:
        return None

    if reconstruction_method == REGULARIZED_RECONSTRUCTION_METHOD:
        return reconstruct_regularized_from_signal_changes(
            changes, geometry, DEFAULT_REGULARIZATION_FACTOR
        )

    return reconstruct_from_signal_changes(changes, geometry)


def create_geometry_response(geometry: SensorGeometry | None) -> dict[str, object]:
    if geometry is None:
        return {"loaded": False}

    return {
        "loaded": True,
        "room": {
            "width_m": geometry.room.width_m,
            "height_m": geometry.room.height_m,
            "grid_cell_m": geometry.room.grid_cell_m,
        },
        "receivers": [
            {
                "id": receiver.identifier,
                "label": receiver.label,
                "x_m": receiver.x_m,
                "y_m": receiver.y_m,
                "z_m": receiver.z_m,
            }
            for receiver in geometry.receivers
        ],
        "radios": [
            {
                "id": radio.identifier,
                "network_name": radio.network_name,
                "hardware_address": radio.hardware_address,
                "x_m": radio.x_m,
                "y_m": radio.y_m,
                "z_m": radio.z_m,
                "band": radio.band,
                "channel": radio.channel,
                "reliability": radio.reliability,
            }
            for radio in geometry.radios
        ],
    }


def get_dashboard_record_paths(directory: Path) -> list[Path]:
    output_path = directory / DEFAULT_OUTPUT_PATH.name
    return [
        output_path,
        create_motion_event_path(output_path),
        create_sensing_event_path(output_path),
    ]


def delete_dashboard_record_files(directory: Path) -> tuple[list[str], list[str]]:
    deleted_files: list[str] = []
    missing_files: list[str] = []

    for record_path in get_dashboard_record_paths(directory):
        if not record_path.exists():
            missing_files.append(record_path.name)
            continue

        if record_path.is_file():
            record_path.unlink()
            deleted_files.append(record_path.name)

    return deleted_files, missing_files


def get_query_value(query: dict[str, list[str]], name: str, default: str) -> str:
    values = query.get(name)
    if not values:
        return default

    return values[-1]


def get_query_values(query: dict[str, list[str]], name: str) -> list[str]:
    return [value.strip() for value in query.get(name, []) if value.strip()]


def parse_excluded_network_names(query: dict[str, list[str]]) -> set[str]:
    if "exclude" not in query:
        return set(DEFAULT_EXCLUDED_NETWORK_NAMES)

    return set(get_query_values(query, "exclude"))


def parse_reconstruction_method(query: dict[str, list[str]]) -> str:
    method = get_query_value(query, "reconstruction", DEFAULT_RECONSTRUCTION_METHOD).strip().casefold()
    if method in SUPPORTED_RECONSTRUCTION_METHODS:
        return method

    return DEFAULT_RECONSTRUCTION_METHOD


def get_positive_int(query: dict[str, list[str]], name: str, default: int) -> int:
    try:
        value = int(get_query_value(query, name, str(default)))
    except ValueError:
        return default

    return value if value > 0 else default


def get_positive_float(query: dict[str, list[str]], name: str, default: float) -> float:
    try:
        value = float(get_query_value(query, name, str(default)))
    except ValueError:
        return default

    return value if value > 0 else default


def sample_to_dict(sample: AccessPointSample) -> dict[str, object]:
    return {
        "collected_at": sample.collected_at,
        "network_name": sample.network_name,
        "hardware_address": sample.hardware_address,
        "signal_percent": sample.signal_percent,
        "band": sample.band,
        "channel": sample.channel,
        "gateway_latency_milliseconds": sample.gateway_latency_milliseconds,
        "gateway_reachable": sample.gateway_reachable,
    }


def baseline_to_dict(baseline: AccessPointBaseline) -> dict[str, object]:
    return {
        "network_name": baseline.network_name,
        "hardware_address": baseline.hardware_address,
        "band": baseline.band,
        "channel": baseline.channel,
        "signal_percent": baseline.signal_percent,
        "sample_count": baseline.sample_count,
        "observed_ratio": baseline.observed_ratio,
        "signal_standard_deviation": baseline.signal_standard_deviation,
        "signal_median_absolute_deviation": baseline.signal_median_absolute_deviation,
        "reliability": baseline.reliability,
    }


def change_to_dict(change: SignalChange) -> dict[str, object]:
    return {
        "network_name": change.network_name,
        "hardware_address": change.hardware_address,
        "band": change.band,
        "channel": change.channel,
        "baseline_signal_percent": change.baseline_signal_percent,
        "current_signal_percent": change.current_signal_percent,
        "signal_delta": change.signal_delta,
        "baseline_signal_standard_deviation": change.baseline_signal_standard_deviation,
        "baseline_signal_median_absolute_deviation": change.baseline_signal_median_absolute_deviation,
        "standardized_reaction": change.standardized_reaction,
        "radio_reliability": change.radio_reliability,
    }


def assessment_to_dict(assessment: MovementAssessment) -> dict[str, object]:
    return {
        "status": assessment.status,
        "score": assessment.score,
        "changed_signal_count": assessment.changed_signal_count,
        "watched_signal_count": assessment.watched_signal_count,
        "strongest_signal_delta": assessment.strongest_signal_delta,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the live WiFi motion dashboard server.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    arguments = parse_arguments()
    handler = lambda *args, **kwargs: LiveMotionHandler(*args, directory=str(arguments.directory), **kwargs)
    server = ThreadingHTTPServer((arguments.host, arguments.port), handler)
    server.remote_receiver_registry = RemoteReceiverRegistry()
    print(f"Live WiFi motion dashboard: http://{arguments.host}:{arguments.port}/wifi_motion_dashboard.html")
    server.serve_forever()


if __name__ == "__main__":
    main()
