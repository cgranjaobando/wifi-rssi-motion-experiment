"""RSSI distance estimation via a log-distance path-loss model.

This module adds a coarse ranging layer on top of the existing RSSI samples.
Windows reports a signal *quality* percentage, which is mapped to an
approximate RSSI in dBm and then converted to an estimated distance with the
classic indoor log-distance path-loss model:

    rssi(d) = rssi_at_1m - 10 * n * log10(d)

where ``n`` is the path-loss exponent. When room geometry is available the
model is calibrated by least-squares against the known router/receiver
distances, so the estimate reflects the actual environment instead of a guess.

This is still RSSI-based ranging. It estimates a radial distance per link with
several meters of uncertainty. It cannot resolve direction, multipath/bounces,
or object identity. Those require phase/CSI or FTM/RTT hardware.
"""

import math
from dataclasses import dataclass
from typing import Sequence

from wifi_geometry import RadioGeometry, ReceiverGeometry, SensorGeometry


MODEL_NAME = "rssi_log_distance_path_loss_v1"

# Microsoft WLAN reports signal quality as 0-100%. The documented mapping to
# RSSI is linear: 0% -> -100 dBm, 100% -> -50 dBm.
MINIMUM_RSSI_DBM = -100.0
MAXIMUM_RSSI_DBM = -50.0

DEFAULT_PATH_LOSS_EXPONENT = 3.0
DEFAULT_REFERENCE_RSSI_DBM = -40.0
REFERENCE_DISTANCE_METERS = 1.0

MINIMUM_PATH_LOSS_EXPONENT = 1.5
MAXIMUM_PATH_LOSS_EXPONENT = 6.0
MINIMUM_DISTANCE_METERS = 0.1
MAXIMUM_DISTANCE_METERS = 100.0
MINIMUM_CALIBRATION_POINTS = 2
MINIMUM_DISTANCE_SPREAD = 1e-6


@dataclass(frozen=True)
class PathLossModel:
    reference_rssi_dbm: float
    path_loss_exponent: float
    calibrated: bool
    sample_count: int
    residual_rms_db: float


@dataclass(frozen=True)
class RangingObservation:
    distance_m: float
    rssi_dbm: float


@dataclass(frozen=True)
class DistanceEstimate:
    network_name: str
    hardware_address: str
    rssi_dbm: float
    estimated_distance_m: float
    lower_distance_m: float
    upper_distance_m: float


def signal_percent_to_dbm(signal_percent: float) -> float:
    """Map a Windows WLAN signal quality percentage to approximate RSSI dBm."""
    clamped_percent = clamp(float(signal_percent), 0.0, 100.0)
    return MINIMUM_RSSI_DBM + (clamped_percent / 100.0) * (
        MAXIMUM_RSSI_DBM - MINIMUM_RSSI_DBM
    )


def estimate_distance_meters(rssi_dbm: float, model: PathLossModel) -> float:
    """Invert the log-distance path-loss model to estimate radial distance."""
    exponent = (model.reference_rssi_dbm - rssi_dbm) / (
        10.0 * model.path_loss_exponent
    )
    distance = REFERENCE_DISTANCE_METERS * (10.0 ** exponent)
    return clamp(distance, MINIMUM_DISTANCE_METERS, MAXIMUM_DISTANCE_METERS)


def default_path_loss_model() -> PathLossModel:
    return PathLossModel(
        reference_rssi_dbm=DEFAULT_REFERENCE_RSSI_DBM,
        path_loss_exponent=DEFAULT_PATH_LOSS_EXPONENT,
        calibrated=False,
        sample_count=0,
        residual_rms_db=0.0,
    )


def calibrate_path_loss(
    observations: Sequence[RangingObservation],
) -> PathLossModel:
    """Fit reference RSSI and path-loss exponent by least squares.

    The model ``rssi = a + b * log10(d)`` is linear in ``log10(d)``. A simple
    ordinary-least-squares fit recovers ``a`` (reference RSSI at 1 m) and
    ``b = -10 * n``. At least two observations at distinct distances are
    required; otherwise the default indoor model is returned.
    """
    usable = [
        observation
        for observation in observations
        if observation.distance_m >= MINIMUM_DISTANCE_METERS
    ]
    if len(usable) < MINIMUM_CALIBRATION_POINTS:
        return default_path_loss_model()

    log_distances = [math.log10(observation.distance_m) for observation in usable]
    rssi_values = [observation.rssi_dbm for observation in usable]
    point_count = len(usable)

    mean_log_distance = sum(log_distances) / point_count
    mean_rssi = sum(rssi_values) / point_count

    covariance = sum(
        (log_distance - mean_log_distance) * (rssi - mean_rssi)
        for log_distance, rssi in zip(log_distances, rssi_values)
    )
    variance = sum(
        (log_distance - mean_log_distance) ** 2 for log_distance in log_distances
    )
    if variance < MINIMUM_DISTANCE_SPREAD:
        return default_path_loss_model()

    slope = covariance / variance
    intercept = mean_rssi - slope * mean_log_distance

    path_loss_exponent = clamp(
        -slope / 10.0,
        MINIMUM_PATH_LOSS_EXPONENT,
        MAXIMUM_PATH_LOSS_EXPONENT,
    )

    residual_rms = calculate_residual_rms(
        log_distances, rssi_values, intercept, slope
    )

    return PathLossModel(
        reference_rssi_dbm=intercept,
        path_loss_exponent=path_loss_exponent,
        calibrated=True,
        sample_count=point_count,
        residual_rms_db=residual_rms,
    )


def calibrate_from_geometry(
    geometry: SensorGeometry,
    radio_signal_percents: dict[str, float],
) -> PathLossModel:
    """Build a calibrated model from configured geometry and observed signals.

    ``radio_signal_percents`` maps a normalized radio hardware address to its
    observed signal quality percentage. The known receiver-to-radio distance
    provides the calibration distance for each link.
    """
    receiver = geometry.receivers[0]
    observations: list[RangingObservation] = []

    for radio in geometry.radios:
        signal_percent = radio_signal_percents.get(radio.hardware_address)
        if signal_percent is None:
            continue
        observations.append(
            RangingObservation(
                distance_m=radio_distance_to_receiver(radio, receiver),
                rssi_dbm=signal_percent_to_dbm(signal_percent),
            )
        )

    return calibrate_path_loss(observations)


def estimate_distances(
    radio_signal_percents: dict[str, float],
    geometry: SensorGeometry,
    model: PathLossModel,
) -> list[DistanceEstimate]:
    estimates: list[DistanceEstimate] = []
    for radio in geometry.radios:
        signal_percent = radio_signal_percents.get(radio.hardware_address)
        if signal_percent is None:
            continue
        rssi_dbm = signal_percent_to_dbm(signal_percent)
        distance = estimate_distance_meters(rssi_dbm, model)
        lower, upper = distance_uncertainty_band(rssi_dbm, model)
        estimates.append(
            DistanceEstimate(
                network_name=radio.network_name,
                hardware_address=radio.hardware_address,
                rssi_dbm=round(rssi_dbm, 2),
                estimated_distance_m=round(distance, 3),
                lower_distance_m=round(lower, 3),
                upper_distance_m=round(upper, 3),
            )
        )
    return estimates


def distance_uncertainty_band(
    rssi_dbm: float,
    model: PathLossModel,
) -> tuple[float, float]:
    """Return a coarse distance band from the fit residual (or a default)."""
    margin_db = model.residual_rms_db if model.residual_rms_db > 0.0 else 4.0
    near = estimate_distance_meters(rssi_dbm + margin_db, model)
    far = estimate_distance_meters(rssi_dbm - margin_db, model)
    return min(near, far), max(near, far)


def radio_distance_to_receiver(
    radio: RadioGeometry,
    receiver: ReceiverGeometry,
) -> float:
    delta_x = radio.x_m - receiver.x_m
    delta_y = radio.y_m - receiver.y_m
    delta_z = radio.z_m - receiver.z_m
    return math.sqrt(delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)


def calculate_residual_rms(
    log_distances: Sequence[float],
    rssi_values: Sequence[float],
    intercept: float,
    slope: float,
) -> float:
    squared_error = sum(
        (rssi - (intercept + slope * log_distance)) ** 2
        for log_distance, rssi in zip(log_distances, rssi_values)
    )
    return math.sqrt(squared_error / len(rssi_values))


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
