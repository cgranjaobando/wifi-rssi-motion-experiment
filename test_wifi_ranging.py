import unittest

from wifi_field_simulator import create_demo_geometry
from wifi_geometry import (
    RadioGeometry,
    ReceiverGeometry,
    RoomGeometry,
    SensorGeometry,
)
from wifi_ranging import (
    DEFAULT_PATH_LOSS_EXPONENT,
    MAXIMUM_RSSI_DBM,
    MINIMUM_RSSI_DBM,
    RangingObservation,
    calibrate_from_geometry,
    calibrate_path_loss,
    default_path_loss_model,
    estimate_distance_meters,
    estimate_distances,
    radio_distance_to_receiver,
    signal_percent_to_dbm,
)


def _varied_distance_geometry() -> SensorGeometry:
    room = RoomGeometry(width_m=10.0, height_m=6.0, grid_cell_m=0.5)
    receiver = ReceiverGeometry(
        identifier="pc_1",
        label="Laptop",
        x_m=0.0,
        y_m=0.0,
        z_m=0.0,
    )
    radios = (
        RadioGeometry("r1", "AP_1", "aa:bb:cc:dd:ee:01", 1.0, 0.0, 0.0, "2.4 GHz", "6", 1.0),
        RadioGeometry("r2", "AP_2", "aa:bb:cc:dd:ee:02", 2.0, 0.0, 0.0, "5 GHz", "36", 1.0),
        RadioGeometry("r3", "AP_3", "aa:bb:cc:dd:ee:03", 4.0, 0.0, 0.0, "2.4 GHz", "11", 1.0),
        RadioGeometry("r4", "AP_4", "aa:bb:cc:dd:ee:04", 8.0, 0.0, 0.0, "5 GHz", "44", 1.0),
    )
    return SensorGeometry(room=room, receivers=(receiver,), radios=radios)


class WifiRangingTests(unittest.TestCase):
    def test_signal_percent_to_dbm_maps_endpoints(self) -> None:
        self.assertEqual(MINIMUM_RSSI_DBM, signal_percent_to_dbm(0))
        self.assertEqual(MAXIMUM_RSSI_DBM, signal_percent_to_dbm(100))
        self.assertEqual(-75.0, signal_percent_to_dbm(50))

    def test_signal_percent_to_dbm_clamps_out_of_range(self) -> None:
        self.assertEqual(MINIMUM_RSSI_DBM, signal_percent_to_dbm(-20))
        self.assertEqual(MAXIMUM_RSSI_DBM, signal_percent_to_dbm(150))

    def test_estimate_distance_increases_as_signal_weakens(self) -> None:
        model = default_path_loss_model()

        near = estimate_distance_meters(-50.0, model)
        far = estimate_distance_meters(-80.0, model)

        self.assertGreater(far, near)

    def test_calibrate_path_loss_recovers_known_exponent(self) -> None:
        reference_rssi = -40.0
        exponent = 2.5
        observations = [
            RangingObservation(
                distance_m=distance,
                rssi_dbm=reference_rssi - 10.0 * exponent * _log10(distance),
            )
            for distance in (1.0, 2.0, 4.0, 8.0)
        ]

        model = calibrate_path_loss(observations)

        self.assertTrue(model.calibrated)
        self.assertAlmostEqual(exponent, model.path_loss_exponent, places=3)
        self.assertAlmostEqual(reference_rssi, model.reference_rssi_dbm, places=3)

    def test_calibrate_path_loss_falls_back_without_enough_points(self) -> None:
        model = calibrate_path_loss([RangingObservation(distance_m=2.0, rssi_dbm=-60.0)])

        self.assertFalse(model.calibrated)
        self.assertEqual(DEFAULT_PATH_LOSS_EXPONENT, model.path_loss_exponent)

    def test_calibrate_from_geometry_uses_configured_distances(self) -> None:
        geometry = _varied_distance_geometry()
        receiver = geometry.receivers[0]
        reference_rssi = -38.0
        exponent = 3.2
        signal_percents = {}
        for radio in geometry.radios:
            distance = radio_distance_to_receiver(radio, receiver)
            rssi = reference_rssi - 10.0 * exponent * _log10(distance)
            signal_percents[radio.hardware_address] = _dbm_to_percent(rssi)

        model = calibrate_from_geometry(geometry, signal_percents)

        self.assertTrue(model.calibrated)
        self.assertEqual(len(geometry.radios), model.sample_count)

    def test_calibrate_from_geometry_falls_back_when_equidistant(self) -> None:
        geometry = create_demo_geometry()
        signal_percents = {
            radio.hardware_address: 55.0 for radio in geometry.radios
        }

        model = calibrate_from_geometry(geometry, signal_percents)

        self.assertFalse(model.calibrated)

    def test_estimate_distances_returns_band_around_estimate(self) -> None:
        geometry = create_demo_geometry()
        model = default_path_loss_model()
        signal_percents = {
            radio.hardware_address: 60.0 for radio in geometry.radios
        }

        estimates = estimate_distances(signal_percents, geometry, model)

        self.assertEqual(len(geometry.radios), len(estimates))
        for estimate in estimates:
            self.assertLessEqual(estimate.lower_distance_m, estimate.estimated_distance_m)
            self.assertLessEqual(estimate.estimated_distance_m, estimate.upper_distance_m)


def _log10(value: float) -> float:
    import math

    return math.log10(value)


def _dbm_to_percent(rssi_dbm: float) -> float:
    return (rssi_dbm - MINIMUM_RSSI_DBM) / (MAXIMUM_RSSI_DBM - MINIMUM_RSSI_DBM) * 100.0


if __name__ == "__main__":
    unittest.main()
