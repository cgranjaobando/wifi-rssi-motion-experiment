import time
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock

from wifi_signal_monitor import AccessPointSample, normalize_hardware_address


MAXIMUM_SUMMARY_SAMPLES = 5


@dataclass(frozen=True)
class RemoteReceiverSamplePacket:
    receiver_id: str
    receiver_label: str
    received_at: str
    samples: tuple[AccessPointSample, ...]


class RemoteReceiverRegistry:
    def __init__(self) -> None:
        self._packets_by_receiver_id: dict[str, RemoteReceiverSamplePacket] = {}
        self._first_seen_by_receiver_id: dict[str, float] = {}
        self._last_seen_by_receiver_id: dict[str, float] = {}
        self._sample_count_by_receiver_id: dict[str, int] = {}
        self._lock = Lock()

    def update(self, packet: RemoteReceiverSamplePacket) -> None:
        now = time.monotonic()
        with self._lock:
            self._packets_by_receiver_id[packet.receiver_id] = packet
            self._first_seen_by_receiver_id.setdefault(packet.receiver_id, now)
            self._last_seen_by_receiver_id[packet.receiver_id] = now
            self._sample_count_by_receiver_id[packet.receiver_id] = (
                self._sample_count_by_receiver_id.get(packet.receiver_id, 0) + len(packet.samples)
            )

    def summarize(self) -> dict[str, object]:
        with self._lock:
            return {
                "receivers": [
                    self._summarize_packet(packet)
                    for packet in sorted(
                        self._packets_by_receiver_id.values(),
                        key=lambda item: item.receiver_id,
                    )
                ]
            }

    def _summarize_packet(self, packet: RemoteReceiverSamplePacket) -> dict[str, object]:
        last_seen_at = self._last_seen_by_receiver_id.get(packet.receiver_id, time.monotonic())
        strongest_samples = sorted(
            packet.samples,
            key=lambda item: item.signal_percent if item.signal_percent is not None else -1,
            reverse=True,
        )[:MAXIMUM_SUMMARY_SAMPLES]
        return {
            "id": packet.receiver_id,
            "label": packet.receiver_label,
            "received_at": packet.received_at,
            "age_seconds": round(max(0.0, time.monotonic() - last_seen_at), 1),
            "total_sample_count": self._sample_count_by_receiver_id.get(packet.receiver_id, 0),
            "latest_sample_count": len(packet.samples),
            "strongest_samples": [sample_to_dict(sample) for sample in strongest_samples],
        }


def create_receiver_sample_packet(payload: dict[str, object]) -> RemoteReceiverSamplePacket:
    receiver_payload = require_mapping(payload.get("receiver"), "receiver")
    receiver_id = require_text(receiver_payload.get("id"), "receiver.id")
    receiver_label = str(receiver_payload.get("label") or receiver_id)
    sample_payloads = require_list(payload.get("samples"), "samples")

    return RemoteReceiverSamplePacket(
        receiver_id=receiver_id,
        receiver_label=receiver_label,
        received_at=current_utc_timestamp(),
        samples=tuple(parse_sample_payload(item) for item in sample_payloads),
    )


def parse_sample_payload(payload: object) -> AccessPointSample:
    sample_payload = require_mapping(payload, "sample")
    return AccessPointSample(
        collected_at=str(sample_payload.get("collected_at") or ""),
        network_name=require_text(sample_payload.get("network_name"), "sample.network_name"),
        hardware_address=normalize_hardware_address(
            require_text(sample_payload.get("hardware_address"), "sample.hardware_address")
        ),
        signal_percent=parse_optional_int(sample_payload.get("signal_percent")),
        band=str(sample_payload.get("band") or ""),
        channel=str(sample_payload.get("channel") or ""),
        gateway_latency_milliseconds=parse_optional_int(
            sample_payload.get("gateway_latency_milliseconds")
        ),
        gateway_reachable=bool(sample_payload.get("gateway_reachable")),
    )


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


def require_mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object")

    return value


def require_list(value: object, field_name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")

    return value


def require_text(value: object, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} is required")

    return text


def parse_optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None

    return int(value)


def current_utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
