import math
from dataclasses import dataclass
from typing import Sequence

from wifi_geometry import (
    RadioGeometry,
    ReceiverGeometry,
    RoomGeometry,
    SensorGeometry,
    find_radio_geometry_by_hardware_address,
)


MODEL_NAME = "rssi_fresnel_backprojection_v1"
REGULARIZED_MODEL_NAME = "rssi_tikhonov_dual_v1"
DEFAULT_REGULARIZATION_FACTOR = 0.1
MINIMUM_REGULARIZATION = 1e-9
SINGULAR_PIVOT_EPSILON = 1e-12
SPEED_OF_LIGHT_METERS_PER_SECOND = 299_792_458.0
MEGAHERTZ_TO_HERTZ = 1_000_000.0
TWO_POINT_FOUR_GIGAHERTZ_BASE_MEGAHERTZ = 2407.0
TWO_POINT_FOUR_GIGAHERTZ_CHANNEL_STEP_MEGAHERTZ = 5.0
TWO_POINT_FOUR_GIGAHERTZ_CHANNEL_FOURTEEN_MEGAHERTZ = 2484.0
FIVE_GIGAHERTZ_BASE_MEGAHERTZ = 5000.0
FIVE_GIGAHERTZ_CHANNEL_STEP_MEGAHERTZ = 5.0
SIX_GIGAHERTZ_BASE_MEGAHERTZ = 5950.0
SIX_GIGAHERTZ_CHANNEL_STEP_MEGAHERTZ = 5.0
DEFAULT_TWO_POINT_FOUR_GIGAHERTZ_MEGAHERTZ = 2437.0
DEFAULT_FIVE_GIGAHERTZ_MEGAHERTZ = 5180.0
DEFAULT_SIX_GIGAHERTZ_MEGAHERTZ = 5975.0
FIRST_TWO_POINT_FOUR_GIGAHERTZ_CHANNEL = 1
LAST_TWO_POINT_FOUR_GIGAHERTZ_CHANNEL = 13
SPECIAL_TWO_POINT_FOUR_GIGAHERTZ_CHANNEL = 14
MINIMUM_LINK_DISTANCE_METERS = 1.0
MINIMUM_BASELINE_DEVIATION_PERCENT = 1.0
SIGNAL_REACTION_SATURATION = 8.0
VISIBLE_CELL_SCORE_RATIO = 0.25
MINIMUM_VISIBLE_CELL_SCORE = 0.05
MINIMUM_CONFIDENCE_LINK_COUNT = 3
MAXIMUM_CONFIDENCE_LINK_COUNT = 8
FIELD_CONFIDENCE_SCALE = 0.85
LINK_COUNT_CONFIDENCE_WEIGHT = 0.25
RELIABILITY_CONFIDENCE_WEIGHT = 0.35
FIELD_SHARPNESS_CONFIDENCE_WEIGHT = 0.25
RESIDUAL_CONFIDENCE_WEIGHT = 0.15
PEAK_TO_MEAN_CONFIDENCE_SPAN = 4.0
NEUTRAL_RESIDUAL_CONFIDENCE = 0.5
GEOMETRY_DIVERSITY_PLACEHOLDER_SCORE = 0.0
DIAGNOSTIC_ROUNDING_PLACES = 4


@dataclass(frozen=True)
class PointGeometry:
    x_m: float
    y_m: float
    z_m: float = 0.0


@dataclass(frozen=True)
class ReconstructionCell:
    x_m: float
    y_m: float
    score: float


@dataclass(frozen=True)
class LinkReaction:
    radio: RadioGeometry
    receiver: ReceiverGeometry
    reaction: float
    raw_delta: float
    reliability: float


def calculate_wavelength_meters(frequency_megahertz: float) -> float:
    return SPEED_OF_LIGHT_METERS_PER_SECOND / (frequency_megahertz * MEGAHERTZ_TO_HERTZ)


def estimate_frequency_megahertz(band: str, channel: str) -> float:
    parsed_channel = parse_channel(channel)
    normalized_band = band.casefold()

    if "6" in normalized_band and parsed_channel is not None:
        return SIX_GIGAHERTZ_BASE_MEGAHERTZ + parsed_channel * SIX_GIGAHERTZ_CHANNEL_STEP_MEGAHERTZ
    if "6" in normalized_band:
        return DEFAULT_SIX_GIGAHERTZ_MEGAHERTZ

    if "5" in normalized_band and parsed_channel is not None:
        return FIVE_GIGAHERTZ_BASE_MEGAHERTZ + parsed_channel * FIVE_GIGAHERTZ_CHANNEL_STEP_MEGAHERTZ
    if "5" in normalized_band:
        return DEFAULT_FIVE_GIGAHERTZ_MEGAHERTZ

    if parsed_channel == SPECIAL_TWO_POINT_FOUR_GIGAHERTZ_CHANNEL:
        return TWO_POINT_FOUR_GIGAHERTZ_CHANNEL_FOURTEEN_MEGAHERTZ
    if parsed_channel is not None and FIRST_TWO_POINT_FOUR_GIGAHERTZ_CHANNEL <= parsed_channel <= LAST_TWO_POINT_FOUR_GIGAHERTZ_CHANNEL:
        return TWO_POINT_FOUR_GIGAHERTZ_BASE_MEGAHERTZ + parsed_channel * TWO_POINT_FOUR_GIGAHERTZ_CHANNEL_STEP_MEGAHERTZ

    return DEFAULT_TWO_POINT_FOUR_GIGAHERTZ_MEGAHERTZ


def parse_channel(channel: str) -> int | None:
    try:
        return int(str(channel).strip())
    except ValueError:
        return None


def calculate_extra_path_meters(
    transmitter: PointGeometry,
    receiver: PointGeometry,
    point: PointGeometry,
) -> float:
    transmitter_distance = calculate_distance_meters(transmitter, point)
    receiver_distance = calculate_distance_meters(point, receiver)
    direct_distance = calculate_distance_meters(transmitter, receiver)
    return max(0.0, transmitter_distance + receiver_distance - direct_distance)


def calculate_fresnel_weight(
    extra_path_meters: float,
    wavelength_meters: float,
    link_distance_meters: float,
    radio_reliability: float,
) -> float:
    fresnel_radius = wavelength_meters / 2.0
    fresnel_score = math.exp(-((extra_path_meters / fresnel_radius) ** 2))
    distance_decay = 1.0 / max(link_distance_meters, MINIMUM_LINK_DISTANCE_METERS)
    return fresnel_score * distance_decay * clamp(radio_reliability, 0.0, 1.0)


def build_reconstruction_grid(room: RoomGeometry) -> list[PointGeometry]:
    points: list[PointGeometry] = []
    x_m = room.grid_cell_m / 2.0
    while x_m < room.width_m:
        y_m = room.grid_cell_m / 2.0
        while y_m < room.height_m:
            points.append(PointGeometry(x_m=x_m, y_m=y_m))
            y_m += room.grid_cell_m
        x_m += room.grid_cell_m
    return points


def build_weight_matrix(
    link_reactions: Sequence[LinkReaction],
    grid_points: Sequence[PointGeometry],
) -> list[list[float]]:
    return [
        [calculate_grid_weight(link_reaction, grid_point) for grid_point in grid_points]
        for link_reaction in link_reactions
    ]


def reconstruct_disturbance_field(
    link_reactions: Sequence[LinkReaction],
    room: RoomGeometry,
) -> dict[str, object] | None:
    if not link_reactions:
        return None

    grid_points = build_reconstruction_grid(room)
    weight_matrix = build_weight_matrix(link_reactions, grid_points)
    scores = calculate_backprojection_scores(link_reactions, weight_matrix)
    return package_reconstruction_field(
        scores=scores,
        grid_points=grid_points,
        link_reactions=link_reactions,
        room=room,
        model_name=MODEL_NAME,
        weight_matrix=weight_matrix,
    )


def package_reconstruction_field(
    scores: Sequence[float],
    grid_points: Sequence[PointGeometry],
    link_reactions: Sequence[LinkReaction],
    room: RoomGeometry,
    model_name: str,
    weight_matrix: Sequence[Sequence[float]] | None = None,
) -> dict[str, object] | None:
    if not scores:
        return None

    maximum_score = max(scores)
    if maximum_score <= 0:
        return None

    normalized_cells = [
        ReconstructionCell(
            x_m=grid_point.x_m,
            y_m=grid_point.y_m,
            score=score / maximum_score,
        )
        for grid_point, score in zip(grid_points, scores, strict=True)
    ]
    visible_threshold = max(MINIMUM_VISIBLE_CELL_SCORE, VISIBLE_CELL_SCORE_RATIO)
    visible_cells = [cell for cell in normalized_cells if cell.score >= visible_threshold]
    peak_cell = max(normalized_cells, key=lambda cell: cell.score)
    centroid = calculate_weighted_centroid(visible_cells or normalized_cells)
    area_m2 = len(visible_cells) * room.grid_cell_m * room.grid_cell_m
    diagnostics = calculate_reconstruction_diagnostics(
        scores=scores,
        link_reactions=link_reactions,
        visible_cell_count=len(visible_cells),
        weight_matrix=weight_matrix,
    )
    normalized_residual_error = diagnostics["normalized_residual_error"]
    confidence = calculate_field_confidence(
        peak_score=peak_cell.score,
        link_count=len(link_reactions),
        average_link_reliability=float(diagnostics["average_link_reliability"]),
        peak_to_mean_ratio=float(diagnostics["peak_to_mean_ratio"]),
        normalized_residual_error=(
            float(normalized_residual_error)
            if isinstance(normalized_residual_error, (int, float))
            else None
        ),
    )

    return {
        "model": model_name,
        "receiver_geometry": classify_receiver_geometry(link_reactions),
        "grid_cell_m": room.grid_cell_m,
        "room": {
            "width_m": room.width_m,
            "height_m": room.height_m,
        },
        "peak": {
            "x_m": round(peak_cell.x_m, 3),
            "y_m": round(peak_cell.y_m, 3),
            "confidence": round(confidence, 3),
        },
        "centroid": {
            "x_m": round(centroid.x_m, 3),
            "y_m": round(centroid.y_m, 3),
        },
        "area_m2": round(area_m2, 3),
        "uncertainty": calculate_uncertainty(len(link_reactions), classify_receiver_geometry(link_reactions)),
        "supporting_links": len(link_reactions),
        "contradicting_links": 0,
        "diagnostics": diagnostics,
        "cells": [
            {
                "x_m": round(cell.x_m, 3),
                "y_m": round(cell.y_m, 3),
                "score": round(cell.score, 4),
            }
            for cell in visible_cells
        ],
    }


def reconstruct_from_signal_changes(
    changes: Sequence[object],
    geometry: SensorGeometry,
) -> dict[str, object] | None:
    link_reactions = create_link_reactions(changes, geometry)
    return reconstruct_disturbance_field(link_reactions, geometry.room)


def reconstruct_regularized_field(
    link_reactions: Sequence[LinkReaction],
    room: RoomGeometry,
    regularization_factor: float = DEFAULT_REGULARIZATION_FACTOR,
) -> dict[str, object] | None:
    if not link_reactions:
        return None

    grid_points = build_reconstruction_grid(room)
    weight_matrix = build_weight_matrix(link_reactions, grid_points)
    scores = solve_regularized_scores(link_reactions, weight_matrix, regularization_factor)
    return package_reconstruction_field(
        scores=scores,
        grid_points=grid_points,
        link_reactions=link_reactions,
        room=room,
        model_name=REGULARIZED_MODEL_NAME,
        weight_matrix=weight_matrix,
    )


def reconstruct_regularized_from_signal_changes(
    changes: Sequence[object],
    geometry: SensorGeometry,
    regularization_factor: float = DEFAULT_REGULARIZATION_FACTOR,
) -> dict[str, object] | None:
    link_reactions = create_link_reactions(changes, geometry)
    return reconstruct_regularized_field(link_reactions, geometry.room, regularization_factor)


def solve_regularized_scores(
    link_reactions: Sequence[LinkReaction],
    weight_matrix: Sequence[Sequence[float]],
    regularization_factor: float,
) -> list[float]:
    """Solve the Tikhonov-regularized inverse in the dual (link) space.

    The forward model is ``y = A x`` where ``A`` is the link-by-cell weight
    matrix and ``x`` is the per-cell disturbance field. Because there are far
    more cells than links, the minimum-norm Tikhonov solution is computed in
    link space as ``x = A^T (A A^T + lambda I)^-1 y``. This de-correlates
    overlapping links and produces sharper localization than plain
    backprojection while staying numerically small (the solved system is only
    ``links x links``). Negative cell estimates are clamped to zero because a
    disturbance field is non-negative.
    """
    if not weight_matrix:
        return []

    link_count = len(weight_matrix)
    cell_count = len(weight_matrix[0])
    reactions = [calculate_reliable_reaction(link_reaction) for link_reaction in link_reactions]

    normalized_matrix = normalize_link_weights(weight_matrix)

    gram_matrix = [
        [
            sum(normalized_matrix[row][index] * normalized_matrix[column][index] for index in range(cell_count))
            for column in range(link_count)
        ]
        for row in range(link_count)
    ]

    diagonal_average = sum(gram_matrix[row][row] for row in range(link_count)) / link_count
    regularization = max(diagonal_average * regularization_factor, MINIMUM_REGULARIZATION)
    for row in range(link_count):
        gram_matrix[row][row] += regularization

    dual_solution = solve_linear_system(gram_matrix, reactions)
    if dual_solution is None:
        return []

    scores = [0.0 for _ in range(cell_count)]
    for link_index in range(link_count):
        dual_value = dual_solution[link_index]
        if dual_value == 0.0:
            continue
        weights = normalized_matrix[link_index]
        for index in range(cell_count):
            scores[index] += weights[index] * dual_value

    return [max(0.0, score) for score in scores]


def normalize_link_weights(
    weight_matrix: Sequence[Sequence[float]],
) -> list[list[float]]:
    """Scale each link row to unit maximum so links contribute on a common scale.

    The Fresnel weights span many orders of magnitude between links, so the raw
    Gram matrix is numerically dominated by a single link. Per-link
    normalization mirrors the backprojection step and keeps the dual solve
    well-conditioned without changing the spatial pattern of each link.
    """
    normalized_matrix: list[list[float]] = []
    for weights in weight_matrix:
        maximum_weight = max((abs(weight) for weight in weights), default=0.0)
        if maximum_weight <= 0.0:
            normalized_matrix.append([0.0 for _ in weights])
            continue
        normalized_matrix.append([weight / maximum_weight for weight in weights])
    return normalized_matrix


def solve_linear_system(
    matrix: Sequence[Sequence[float]],
    vector: Sequence[float],
) -> list[float] | None:
    """Solve ``matrix x = vector`` via Gaussian elimination with partial pivoting."""
    size = len(matrix)
    if size == 0 or any(len(row) != size for row in matrix) or len(vector) != size:
        return None

    augmented = [[float(value) for value in row] + [float(vector[row_index])] for row_index, row in enumerate(matrix)]

    for pivot_index in range(size):
        pivot_row = max(range(pivot_index, size), key=lambda row: abs(augmented[row][pivot_index]))
        if abs(augmented[pivot_row][pivot_index]) <= SINGULAR_PIVOT_EPSILON:
            return None
        augmented[pivot_index], augmented[pivot_row] = augmented[pivot_row], augmented[pivot_index]

        pivot_value = augmented[pivot_index][pivot_index]
        for row in range(size):
            if row == pivot_index:
                continue
            factor = augmented[row][pivot_index] / pivot_value
            if factor == 0.0:
                continue
            for column in range(pivot_index, size + 1):
                augmented[row][column] -= factor * augmented[pivot_index][column]

    return [augmented[row][size] / augmented[row][row] for row in range(size)]


def create_link_reactions(
    changes: Sequence[object],
    geometry: SensorGeometry,
) -> list[LinkReaction]:
    receiver = geometry.receivers[0]
    link_reactions: list[LinkReaction] = []

    for change in changes:
        radio = find_radio_geometry_by_hardware_address(
            geometry,
            hardware_address=str(getattr(change, "hardware_address")),
        )
        if radio is None:
            continue

        raw_delta = float(getattr(change, "signal_delta"))
        standardized_reaction = float(
            getattr(
                change,
                "standardized_reaction",
                abs(raw_delta) / MINIMUM_BASELINE_DEVIATION_PERCENT,
            )
        )
        signal_reliability = float(getattr(change, "radio_reliability", 1.0))
        link_reactions.append(
            LinkReaction(
                radio=radio,
                receiver=receiver,
                reaction=min(standardized_reaction, SIGNAL_REACTION_SATURATION),
                raw_delta=raw_delta,
                reliability=clamp(radio.reliability * signal_reliability, 0.0, 1.0),
            )
        )

    return link_reactions


def calculate_grid_weight(link_reaction: LinkReaction, grid_point: PointGeometry) -> float:
    transmitter = PointGeometry(
        x_m=link_reaction.radio.x_m,
        y_m=link_reaction.radio.y_m,
        z_m=link_reaction.radio.z_m,
    )
    receiver = PointGeometry(
        x_m=link_reaction.receiver.x_m,
        y_m=link_reaction.receiver.y_m,
        z_m=link_reaction.receiver.z_m,
    )
    frequency_megahertz = estimate_frequency_megahertz(link_reaction.radio.band, link_reaction.radio.channel)
    wavelength_meters = calculate_wavelength_meters(frequency_megahertz)
    link_distance_meters = calculate_distance_meters(transmitter, receiver)
    extra_path_meters = calculate_extra_path_meters(transmitter, receiver, grid_point)
    return calculate_fresnel_weight(
        extra_path_meters,
        wavelength_meters,
        link_distance_meters,
        link_reaction.reliability,
    )


def calculate_backprojection_scores(
    link_reactions: Sequence[LinkReaction],
    weight_matrix: Sequence[Sequence[float]],
) -> list[float]:
    if not weight_matrix:
        return []

    cell_count = len(weight_matrix[0])
    scores = [0.0 for _ in range(cell_count)]

    for link_reaction, weights in zip(link_reactions, weight_matrix, strict=True):
        maximum_weight = max(weights) if weights else 0.0
        if maximum_weight <= 0:
            continue
        reliable_reaction = calculate_reliable_reaction(link_reaction)
        for index, weight in enumerate(weights):
            scores[index] += reliable_reaction * (weight / maximum_weight)

    return scores


def calculate_reliable_reaction(link_reaction: LinkReaction) -> float:
    return link_reaction.reaction * clamp(link_reaction.reliability, 0.0, 1.0)


def calculate_reconstruction_diagnostics(
    scores: Sequence[float],
    link_reactions: Sequence[LinkReaction],
    visible_cell_count: int,
    weight_matrix: Sequence[Sequence[float]] | None,
) -> dict[str, object]:
    normalized_residual_error = calculate_normalized_residual_error(
        link_reactions=link_reactions,
        weight_matrix=weight_matrix,
        scores=scores,
    )
    return {
        "average_link_reliability": round(
            calculate_average_link_reliability(link_reactions),
            DIAGNOSTIC_ROUNDING_PLACES,
        ),
        "normalized_residual_error": (
            round(normalized_residual_error, DIAGNOSTIC_ROUNDING_PLACES)
            if normalized_residual_error is not None
            else None
        ),
        "peak_to_mean_ratio": round(calculate_peak_to_mean_ratio(scores), DIAGNOSTIC_ROUNDING_PLACES),
        "visible_cell_count": visible_cell_count,
        "geometry_diversity_score": GEOMETRY_DIVERSITY_PLACEHOLDER_SCORE,
    }


def calculate_average_link_reliability(link_reactions: Sequence[LinkReaction]) -> float:
    if not link_reactions:
        return 0.0

    return sum(clamp(link_reaction.reliability, 0.0, 1.0) for link_reaction in link_reactions) / len(link_reactions)


def calculate_peak_to_mean_ratio(scores: Sequence[float]) -> float:
    if not scores:
        return 0.0

    mean_score = sum(scores) / len(scores)
    if mean_score <= 0.0:
        return 0.0

    return max(scores) / mean_score


def calculate_normalized_residual_error(
    link_reactions: Sequence[LinkReaction],
    weight_matrix: Sequence[Sequence[float]] | None,
    scores: Sequence[float],
) -> float | None:
    if weight_matrix is None or not weight_matrix or not scores:
        return None
    if len(weight_matrix) != len(link_reactions):
        return None

    cell_count = len(scores)
    normalized_matrix = normalize_link_weights(weight_matrix)
    if any(len(weights) != cell_count for weights in normalized_matrix):
        return None

    observed_reactions = [calculate_reliable_reaction(link_reaction) for link_reaction in link_reactions]
    observed_norm = math.sqrt(sum(reaction * reaction for reaction in observed_reactions))
    if observed_norm <= 0.0:
        return None

    predicted_reactions = [
        sum(weight * scores[index] for index, weight in enumerate(weights))
        for weights in normalized_matrix
    ]
    residual_norm = math.sqrt(
        sum(
            (predicted - observed) * (predicted - observed)
            for predicted, observed in zip(predicted_reactions, observed_reactions, strict=True)
        )
    )
    return residual_norm / observed_norm


def calculate_weighted_centroid(cells: Sequence[ReconstructionCell]) -> PointGeometry:
    total_score = sum(cell.score for cell in cells)
    if total_score <= 0:
        return PointGeometry(x_m=0.0, y_m=0.0)

    return PointGeometry(
        x_m=sum(cell.x_m * cell.score for cell in cells) / total_score,
        y_m=sum(cell.y_m * cell.score for cell in cells) / total_score,
    )


def calculate_field_confidence(
    peak_score: float,
    link_count: int,
    average_link_reliability: float,
    peak_to_mean_ratio: float,
    normalized_residual_error: float | None,
) -> float:
    link_factor = clamp(
        link_count / MAXIMUM_CONFIDENCE_LINK_COUNT,
        1.0 / MINIMUM_CONFIDENCE_LINK_COUNT,
        1.0,
    )
    reliability_factor = clamp(average_link_reliability, 0.0, 1.0)
    sharpness_factor = clamp(peak_to_mean_ratio / PEAK_TO_MEAN_CONFIDENCE_SPAN, 0.0, 1.0)
    residual_factor = calculate_residual_confidence(normalized_residual_error)
    weighted_score = (
        LINK_COUNT_CONFIDENCE_WEIGHT * link_factor
        + RELIABILITY_CONFIDENCE_WEIGHT * reliability_factor
        + FIELD_SHARPNESS_CONFIDENCE_WEIGHT * clamp(peak_score, 0.0, 1.0) * sharpness_factor
        + RESIDUAL_CONFIDENCE_WEIGHT * residual_factor
    )
    return clamp(weighted_score * FIELD_CONFIDENCE_SCALE, 0.0, 1.0)


def calculate_residual_confidence(normalized_residual_error: float | None) -> float:
    if normalized_residual_error is None:
        return NEUTRAL_RESIDUAL_CONFIDENCE

    return 1.0 / (1.0 + max(0.0, normalized_residual_error))


def calculate_uncertainty(link_count: int, receiver_geometry: str) -> str:
    if receiver_geometry == "single_receiver_fan":
        return "high_single_receiver_geometry"
    if link_count < MINIMUM_CONFIDENCE_LINK_COUNT:
        return "high_low_link_count"
    if link_count < MAXIMUM_CONFIDENCE_LINK_COUNT:
        return "medium"
    return "medium_low"


def classify_receiver_geometry(link_reactions: Sequence[LinkReaction]) -> str:
    receiver_identifiers = {link_reaction.receiver.identifier for link_reaction in link_reactions}
    if len(receiver_identifiers) <= 1:
        return "single_receiver_fan"
    return "multi_receiver"


def calculate_distance_meters(first_point: PointGeometry, second_point: PointGeometry) -> float:
    delta_x = first_point.x_m - second_point.x_m
    delta_y = first_point.y_m - second_point.y_m
    delta_z = first_point.z_m - second_point.z_m
    return math.sqrt(delta_x * delta_x + delta_y * delta_y + delta_z * delta_z)


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))
