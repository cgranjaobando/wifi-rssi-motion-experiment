import json
from dataclasses import dataclass
from pathlib import Path


DEFAULT_GEOMETRY_PATH = Path("config/room_geometry.json")


@dataclass(frozen=True)
class RoomGeometry:
    width_m: float
    height_m: float
    grid_cell_m: float


@dataclass(frozen=True)
class ReceiverGeometry:
    identifier: str
    label: str
    x_m: float
    y_m: float
    z_m: float


@dataclass(frozen=True)
class RadioGeometry:
    identifier: str
    network_name: str
    hardware_address: str
    x_m: float
    y_m: float
    z_m: float
    band: str
    channel: str
    reliability: float = 1.0


@dataclass(frozen=True)
class SensorGeometry:
    room: RoomGeometry
    receivers: tuple[ReceiverGeometry, ...]
    radios: tuple[RadioGeometry, ...]


def load_room_geometry(path: str | Path) -> SensorGeometry:
    with Path(path).open("r", encoding="utf-8") as input_file:
        payload = json.load(input_file)

    return parse_room_geometry(payload)


def load_room_geometry_if_available(path: str | Path) -> SensorGeometry | None:
    geometry_path = Path(path)
    if not geometry_path.exists():
        return None

    return load_room_geometry(geometry_path)


def parse_room_geometry(payload: dict[str, object]) -> SensorGeometry:
    room_payload = require_mapping(payload, "room")
    receiver_payloads = require_list(payload, "receivers")
    radio_payloads = require_list(payload, "radios")

    room = RoomGeometry(
        width_m=require_positive_float(room_payload, "width_m"),
        height_m=require_positive_float(room_payload, "height_m"),
        grid_cell_m=require_positive_float(room_payload, "grid_cell_m"),
    )
    receivers = tuple(parse_receiver_geometry(item) for item in receiver_payloads)
    radios = tuple(parse_radio_geometry(item) for item in radio_payloads)

    if not receivers:
        raise ValueError("geometry must include at least one receiver")

    return SensorGeometry(room=room, receivers=receivers, radios=radios)


def parse_receiver_geometry(payload: object) -> ReceiverGeometry:
    receiver_payload = ensure_mapping(payload, "receiver")
    return ReceiverGeometry(
        identifier=require_text(receiver_payload, "id"),
        label=str(receiver_payload.get("label", "")),
        x_m=require_float(receiver_payload, "x_m"),
        y_m=require_float(receiver_payload, "y_m"),
        z_m=require_float(receiver_payload, "z_m"),
    )


def parse_radio_geometry(payload: object) -> RadioGeometry:
    radio_payload = ensure_mapping(payload, "radio")
    return RadioGeometry(
        identifier=require_text(radio_payload, "id"),
        network_name=require_text(radio_payload, "network_name"),
        hardware_address=normalize_hardware_address(require_text(radio_payload, "hardware_address")),
        x_m=require_float(radio_payload, "x_m"),
        y_m=require_float(radio_payload, "y_m"),
        z_m=require_float(radio_payload, "z_m"),
        band=str(radio_payload.get("band", "")),
        channel=str(radio_payload.get("channel", "")),
        reliability=clamp(require_optional_float(radio_payload, "reliability", 1.0), 0.0, 1.0),
    )


def find_radio_geometry(
    geometry: SensorGeometry,
    network_name: str,
    hardware_address: str,
) -> RadioGeometry | None:
    hardware_match = find_radio_geometry_by_hardware_address(geometry, hardware_address)
    if hardware_match is not None:
        return hardware_match

    normalized_network_name = network_name.casefold()

    for radio in geometry.radios:
        if radio.network_name.casefold() == normalized_network_name:
            return radio

    return None


def find_radio_geometry_by_hardware_address(
    geometry: SensorGeometry,
    hardware_address: str,
) -> RadioGeometry | None:
    normalized_address = normalize_hardware_address(hardware_address)
    if not normalized_address:
        return None

    for radio in geometry.radios:
        if radio.hardware_address == normalized_address:
            return radio

    return None


def normalize_hardware_address(value: str) -> str:
    return value.strip().lower().replace("-", ":")


def require_mapping(payload: dict[str, object], key: str) -> dict[str, object]:
    return ensure_mapping(payload.get(key), key)


def ensure_mapping(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def require_list(payload: dict[str, object], key: str) -> list[object]:
    value = payload.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return value


def require_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value.strip()


def require_positive_float(payload: dict[str, object], key: str) -> float:
    value = require_float(payload, key)
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def require_float(payload: dict[str, object], key: str) -> float:
    value = payload.get(key)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def require_optional_float(payload: dict[str, object], key: str, default: float) -> float:
    value = payload.get(key, default)
    if not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
