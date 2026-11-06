from dataclasses import dataclass

from wifi_field_model import (
    LinkReaction,
    PointGeometry,
    calculate_distance_meters,
    calculate_grid_weight,
    reconstruct_disturbance_field,
    reconstruct_regularized_field,
)
from wifi_geometry import (
    RadioGeometry,
    ReceiverGeometry,
    RoomGeometry,
    SensorGeometry,
)


@dataclass(frozen=True)
class SimulationResult:
    reconstruction: dict[str, object] | None
    peak_error_m: float | None
    centroid_error_m: float | None
    supporting_links: int


@dataclass(frozen=True)
class MethodComparison:
    backprojection: SimulationResult
    regularized: SimulationResult


def create_demo_geometry() -> SensorGeometry:
    room = RoomGeometry(width_m=5.8, height_m=4.2, grid_cell_m=0.25)
    receiver = ReceiverGeometry(
        identifier="pc_1",
        label="Laptop receiver",
        x_m=2.9,
        y_m=2.1,
        z_m=0.9,
    )
    radios = (
        RadioGeometry("radio_001", "AP_1", "aa:bb:cc:dd:ee:01", 0.5, 0.5, 1.8, "2.4 GHz", "6", 0.9),
        RadioGeometry("radio_002", "AP_2", "aa:bb:cc:dd:ee:02", 5.3, 0.5, 1.8, "5 GHz", "36", 0.85),
        RadioGeometry("radio_003", "AP_3", "aa:bb:cc:dd:ee:03", 5.3, 3.7, 1.8, "2.4 GHz", "11", 0.85),
        RadioGeometry("radio_004", "AP_4", "aa:bb:cc:dd:ee:04", 0.5, 3.7, 1.8, "5 GHz", "44", 0.85),
    )
    return SensorGeometry(room=room, receivers=(receiver,), radios=radios)


def simulate_link_reactions(
    geometry: SensorGeometry,
    disturbance: PointGeometry,
    reaction_scale: float = 8.0,
) -> list[LinkReaction]:
    receiver = geometry.receivers[0]
    raw_reactions = [
        LinkReaction(
            radio=radio,
            receiver=receiver,
            reaction=calculate_grid_weight(
                LinkReaction(
                    radio=radio,
                    receiver=receiver,
                    reaction=1.0,
                    raw_delta=1.0,
                    reliability=radio.reliability,
                ),
                disturbance,
            ),
            raw_delta=0.0,
            reliability=radio.reliability,
        )
        for radio in geometry.radios
    ]
    maximum_reaction = max((link.reaction for link in raw_reactions), default=1.0)
    if maximum_reaction <= 0:
        return []

    return [
        LinkReaction(
            radio=link.radio,
            receiver=link.receiver,
            reaction=(link.reaction / maximum_reaction) * reaction_scale,
            raw_delta=(link.reaction / maximum_reaction) * reaction_scale,
            reliability=link.reliability,
        )
        for link in raw_reactions
    ]


def run_synthetic_room_simulation() -> SimulationResult:
    geometry = create_demo_geometry()
    disturbance = PointGeometry(x_m=2.1, y_m=1.6)
    link_reactions = simulate_link_reactions(geometry, disturbance)
    reconstruction = reconstruct_disturbance_field(link_reactions, geometry.room)
    return score_reconstruction(reconstruction, disturbance, len(link_reactions))


def run_method_comparison() -> MethodComparison:
    geometry = create_demo_geometry()
    disturbance = PointGeometry(x_m=2.1, y_m=1.6)
    link_reactions = simulate_link_reactions(geometry, disturbance)

    backprojection = score_reconstruction(
        reconstruct_disturbance_field(link_reactions, geometry.room),
        disturbance,
        len(link_reactions),
    )
    regularized = score_reconstruction(
        reconstruct_regularized_field(link_reactions, geometry.room),
        disturbance,
        len(link_reactions),
    )
    return MethodComparison(backprojection=backprojection, regularized=regularized)


def score_reconstruction(
    reconstruction: dict[str, object] | None,
    disturbance: PointGeometry,
    supporting_links: int,
) -> SimulationResult:
    if reconstruction is None:
        return SimulationResult(
            reconstruction=None,
            peak_error_m=None,
            centroid_error_m=None,
            supporting_links=supporting_links,
        )

    peak = reconstruction["peak"]
    centroid = reconstruction["centroid"]
    peak_error = calculate_distance_meters(
        disturbance,
        PointGeometry(x_m=float(peak["x_m"]), y_m=float(peak["y_m"])),
    )
    centroid_error = calculate_distance_meters(
        disturbance,
        PointGeometry(x_m=float(centroid["x_m"]), y_m=float(centroid["y_m"])),
    )

    return SimulationResult(
        reconstruction=reconstruction,
        peak_error_m=peak_error,
        centroid_error_m=centroid_error,
        supporting_links=supporting_links,
    )


def format_error(value: float | None) -> str:
    return f"{value:.3f}" if value is not None else "n/a"


def main() -> None:
    comparison = run_method_comparison()
    print(f"supporting_links={comparison.backprojection.supporting_links}")
    for label, result in (
        ("backprojection", comparison.backprojection),
        ("regularized", comparison.regularized),
    ):
        print(
            f"{label}: peak_error_m={format_error(result.peak_error_m)} "
            f"centroid_error_m={format_error(result.centroid_error_m)}"
        )


if __name__ == "__main__":
    main()
