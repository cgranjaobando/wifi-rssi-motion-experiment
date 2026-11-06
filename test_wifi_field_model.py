import unittest

from wifi_field_model import (
    LinkReaction,
    PointGeometry,
    build_weight_matrix,
    build_reconstruction_grid,
    calculate_backprojection_scores,
    calculate_extra_path_meters,
    calculate_fresnel_weight,
    calculate_wavelength_meters,
    create_link_reactions,
    estimate_frequency_megahertz,
    reconstruct_disturbance_field,
    reconstruct_regularized_field,
    solve_regularized_scores,
    solve_linear_system,
)
from wifi_field_simulator import create_demo_geometry, simulate_link_reactions
from wifi_geometry import RadioGeometry, ReceiverGeometry, RoomGeometry, SensorGeometry
from wifi_signal_monitor import SignalChange


def create_link_reaction(reliability: float) -> LinkReaction:
    radio = RadioGeometry(
        identifier="radio",
        network_name="TestRadio",
        hardware_address="aa:bb:cc:dd:ee:ff",
        x_m=0.0,
        y_m=0.0,
        z_m=1.0,
        band="2.4 GHz",
        channel="6",
        reliability=1.0,
    )
    receiver = ReceiverGeometry(
        identifier="receiver",
        label="Receiver",
        x_m=4.0,
        y_m=0.0,
        z_m=1.0,
    )
    return LinkReaction(
        radio=radio,
        receiver=receiver,
        reaction=4.0,
        raw_delta=4.0,
        reliability=reliability,
    )


class WifiFieldModelTests(unittest.TestCase):
    def test_calculate_wavelength_meters_matches_wifi_scale(self) -> None:
        wavelength = calculate_wavelength_meters(2400)

        self.assertAlmostEqual(0.125, wavelength, places=3)

    def test_estimate_frequency_megahertz_uses_band_and_channel(self) -> None:
        self.assertEqual(2437.0, estimate_frequency_megahertz("2.4 GHz", "6"))
        self.assertEqual(5180.0, estimate_frequency_megahertz("5 GHz", "36"))

    def test_calculate_extra_path_meters_is_zero_on_direct_line(self) -> None:
        transmitter = PointGeometry(x_m=0.0, y_m=0.0)
        receiver = PointGeometry(x_m=4.0, y_m=0.0)
        point = PointGeometry(x_m=2.0, y_m=0.0)

        self.assertEqual(0.0, calculate_extra_path_meters(transmitter, receiver, point))

    def test_calculate_fresnel_weight_falls_with_extra_path(self) -> None:
        wavelength = calculate_wavelength_meters(2400)

        direct_weight = calculate_fresnel_weight(
            extra_path_meters=0.0,
            wavelength_meters=wavelength,
            link_distance_meters=4.0,
            radio_reliability=1.0,
        )
        offset_weight = calculate_fresnel_weight(
            extra_path_meters=0.2,
            wavelength_meters=wavelength,
            link_distance_meters=4.0,
            radio_reliability=1.0,
        )

        self.assertGreater(direct_weight, offset_weight)

    def test_build_reconstruction_grid_uses_cell_centers(self) -> None:
        grid = build_reconstruction_grid(RoomGeometry(width_m=1.0, height_m=1.0, grid_cell_m=0.5))

        self.assertEqual(4, len(grid))
        self.assertEqual(PointGeometry(x_m=0.25, y_m=0.25, z_m=0.0), grid[0])

    def test_reconstruct_disturbance_field_returns_peak_near_synthetic_disturbance(self) -> None:
        geometry = create_demo_geometry()
        disturbance = PointGeometry(x_m=2.1, y_m=1.6)
        link_reactions = simulate_link_reactions(geometry, disturbance)

        reconstruction = reconstruct_disturbance_field(link_reactions, geometry.room)

        self.assertIsNotNone(reconstruction)
        peak = reconstruction["peak"]
        self.assertLess(abs(float(peak["x_m"]) - disturbance.x_m), 1.2)
        self.assertLess(abs(float(peak["y_m"]) - disturbance.y_m), 1.2)
        self.assertEqual("rssi_fresnel_backprojection_v1", reconstruction["model"])
        self.assertGreater(reconstruction["supporting_links"], 0)

    def test_backprojection_preserves_reliability_after_row_normalization(self) -> None:
        grid_points = [PointGeometry(x_m=2.0, y_m=0.0)]
        high_reliability_link = create_link_reaction(reliability=1.0)
        low_reliability_link = create_link_reaction(reliability=0.25)

        high_scores = calculate_backprojection_scores(
            [high_reliability_link],
            build_weight_matrix([high_reliability_link], grid_points),
        )
        low_scores = calculate_backprojection_scores(
            [low_reliability_link],
            build_weight_matrix([low_reliability_link], grid_points),
        )

        self.assertLess(low_scores[0], high_scores[0])
        self.assertAlmostEqual(high_scores[0] * 0.25, low_scores[0], places=6)

    def test_reconstruction_includes_diagnostics(self) -> None:
        room = RoomGeometry(width_m=4.0, height_m=1.0, grid_cell_m=1.0)
        link_reaction = create_link_reaction(reliability=0.75)

        reconstruction = reconstruct_disturbance_field([link_reaction], room)

        self.assertIsNotNone(reconstruction)
        diagnostics = reconstruction["diagnostics"]
        self.assertEqual(0.75, diagnostics["average_link_reliability"])
        self.assertIn("normalized_residual_error", diagnostics)
        self.assertGreater(diagnostics["peak_to_mean_ratio"], 0.0)
        self.assertEqual(len(reconstruction["cells"]), diagnostics["visible_cell_count"])
        self.assertEqual(0.0, diagnostics["geometry_diversity_score"])

    def test_reconstruction_confidence_decreases_with_average_reliability(self) -> None:
        room = RoomGeometry(width_m=4.0, height_m=1.0, grid_cell_m=1.0)
        high_reliability_links = [create_link_reaction(reliability=1.0), create_link_reaction(reliability=1.0)]
        low_reliability_links = [create_link_reaction(reliability=0.25), create_link_reaction(reliability=0.25)]

        high_reliability_reconstruction = reconstruct_disturbance_field(high_reliability_links, room)
        low_reliability_reconstruction = reconstruct_disturbance_field(low_reliability_links, room)

        self.assertIsNotNone(high_reliability_reconstruction)
        self.assertIsNotNone(low_reliability_reconstruction)
        self.assertLess(
            low_reliability_reconstruction["peak"]["confidence"],
            high_reliability_reconstruction["peak"]["confidence"],
        )

    def test_create_link_reactions_uses_matching_hardware_address_for_shared_ssid(self) -> None:
        geometry = SensorGeometry(
            room=RoomGeometry(width_m=4.0, height_m=3.0, grid_cell_m=1.0),
            receivers=(
                ReceiverGeometry(
                    identifier="pc",
                    label="PC",
                    x_m=2.0,
                    y_m=1.5,
                    z_m=1.0,
                ),
            ),
            radios=(
                RadioGeometry(
                    identifier="wrong_radio",
                    network_name="SharedSSID",
                    hardware_address="aa:bb:cc:dd:ee:01",
                    x_m=0.0,
                    y_m=0.0,
                    z_m=1.0,
                    band="2.4 GHz",
                    channel="6",
                ),
                RadioGeometry(
                    identifier="correct_radio",
                    network_name="SharedSSID",
                    hardware_address="aa:bb:cc:dd:ee:02",
                    x_m=4.0,
                    y_m=3.0,
                    z_m=1.0,
                    band="5 GHz",
                    channel="36",
                ),
            ),
        )
        change = SignalChange(
            network_name="SharedSSID",
            hardware_address="AA-BB-CC-DD-EE-02",
            band="5 GHz",
            channel="36",
            baseline_signal_percent=40.0,
            current_signal_percent=47,
            signal_delta=7.0,
            baseline_signal_standard_deviation=1.0,
            baseline_signal_median_absolute_deviation=1.0,
            standardized_reaction=7.0,
            radio_reliability=1.0,
        )

        link_reactions = create_link_reactions([change], geometry)

        self.assertEqual(1, len(link_reactions))
        self.assertEqual("correct_radio", link_reactions[0].radio.identifier)

    def test_create_link_reactions_skips_ssid_only_match(self) -> None:
        geometry = SensorGeometry(
            room=RoomGeometry(width_m=4.0, height_m=3.0, grid_cell_m=1.0),
            receivers=(
                ReceiverGeometry(
                    identifier="pc",
                    label="PC",
                    x_m=2.0,
                    y_m=1.5,
                    z_m=1.0,
                ),
            ),
            radios=(
                RadioGeometry(
                    identifier="same_name_radio",
                    network_name="SharedSSID",
                    hardware_address="aa:bb:cc:dd:ee:01",
                    x_m=0.0,
                    y_m=0.0,
                    z_m=1.0,
                    band="2.4 GHz",
                    channel="6",
                ),
            ),
        )
        change = SignalChange(
            network_name="SharedSSID",
            hardware_address="aa:bb:cc:dd:ee:99",
            band="2.4 GHz",
            channel="6",
            baseline_signal_percent=40.0,
            current_signal_percent=47,
            signal_delta=7.0,
            baseline_signal_standard_deviation=1.0,
            baseline_signal_median_absolute_deviation=1.0,
            standardized_reaction=7.0,
            radio_reliability=1.0,
        )

        self.assertEqual([], create_link_reactions([change], geometry))


class TikhonovSolverTests(unittest.TestCase):
    def test_solve_linear_system_recovers_known_solution(self) -> None:
        matrix = [[2.0, 1.0], [1.0, 3.0]]
        vector = [5.0, 10.0]

        solution = solve_linear_system(matrix, vector)

        self.assertIsNotNone(solution)
        self.assertAlmostEqual(1.0, solution[0], places=6)
        self.assertAlmostEqual(3.0, solution[1], places=6)

    def test_solve_linear_system_returns_none_for_singular_matrix(self) -> None:
        matrix = [[1.0, 2.0], [2.0, 4.0]]
        vector = [1.0, 2.0]

        self.assertIsNone(solve_linear_system(matrix, vector))

    def test_solve_linear_system_returns_none_for_shape_mismatch(self) -> None:
        self.assertIsNone(solve_linear_system([[1.0, 2.0]], [1.0]))

    def test_regularized_field_locates_synthetic_disturbance(self) -> None:
        geometry = create_demo_geometry()
        disturbance = PointGeometry(x_m=2.1, y_m=1.6)
        link_reactions = simulate_link_reactions(geometry, disturbance)

        reconstruction = reconstruct_regularized_field(link_reactions, geometry.room)

        self.assertIsNotNone(reconstruction)
        self.assertEqual("rssi_tikhonov_dual_v1", reconstruction["model"])
        peak = reconstruction["peak"]
        self.assertLess(abs(float(peak["x_m"]) - disturbance.x_m), 1.2)
        self.assertLess(abs(float(peak["y_m"]) - disturbance.y_m), 1.2)
        for cell in reconstruction["cells"]:
            self.assertGreaterEqual(float(cell["score"]), 0.0)

    def test_regularized_field_returns_none_without_links(self) -> None:
        room = RoomGeometry(width_m=2.0, height_m=2.0, grid_cell_m=0.5)

        self.assertIsNone(reconstruct_regularized_field([], room))

    def test_regularized_scores_preserve_reliability_after_row_normalization(self) -> None:
        grid_points = [PointGeometry(x_m=2.0, y_m=0.0)]
        high_reliability_link = create_link_reaction(reliability=1.0)
        low_reliability_link = create_link_reaction(reliability=0.25)

        high_scores = solve_regularized_scores(
            [high_reliability_link],
            build_weight_matrix([high_reliability_link], grid_points),
            regularization_factor=0.0,
        )
        low_scores = solve_regularized_scores(
            [low_reliability_link],
            build_weight_matrix([low_reliability_link], grid_points),
            regularization_factor=0.0,
        )

        self.assertLess(low_scores[0], high_scores[0])
        self.assertAlmostEqual(high_scores[0] * 0.25, low_scores[0], places=6)


if __name__ == "__main__":
    unittest.main()
