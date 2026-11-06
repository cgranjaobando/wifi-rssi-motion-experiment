import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path

from wifi_geometry import (
    find_radio_geometry,
    load_room_geometry,
    parse_room_geometry,
)


class WifiGeometryTests(unittest.TestCase):
    def test_parse_room_geometry_returns_typed_geometry(self) -> None:
        geometry = parse_room_geometry(
            {
                "room": {"width_m": 5.8, "height_m": 4.2, "grid_cell_m": 0.25},
                "receivers": [
                    {"id": "pc_1", "label": "Laptop", "x_m": 2.9, "y_m": 2.1, "z_m": 0.9}
                ],
                "radios": [
                    {
                        "id": "radio_001",
                        "network_name": "Neighbor",
                        "hardware_address": "AA-BB-CC-DD-EE-01",
                        "x_m": 0.8,
                        "y_m": 0.5,
                        "z_m": 1.8,
                        "band": "2.4 GHz",
                        "channel": "6",
                        "reliability": 0.8,
                    }
                ],
            }
        )

        self.assertEqual(5.8, geometry.room.width_m)
        self.assertEqual("pc_1", geometry.receivers[0].identifier)
        self.assertEqual("aa:bb:cc:dd:ee:01", geometry.radios[0].hardware_address)
        self.assertEqual(0.8, geometry.radios[0].reliability)

    def test_find_radio_geometry_matches_hardware_address_first(self) -> None:
        geometry = parse_room_geometry(
            {
                "room": {"width_m": 5.8, "height_m": 4.2, "grid_cell_m": 0.25},
                "receivers": [
                    {"id": "pc_1", "label": "Laptop", "x_m": 2.9, "y_m": 2.1, "z_m": 0.9}
                ],
                "radios": [
                    {
                        "id": "radio_001",
                        "network_name": "Neighbor",
                        "hardware_address": "aa:bb:cc:dd:ee:01",
                        "x_m": 0.8,
                        "y_m": 0.5,
                        "z_m": 1.8,
                        "band": "2.4 GHz",
                        "channel": "6",
                    }
                ],
            }
        )

        radio = find_radio_geometry(geometry, "DifferentName", "AA-BB-CC-DD-EE-01")

        self.assertIsNotNone(radio)
        self.assertEqual("radio_001", radio.identifier)

    def test_load_room_geometry_reads_json_file(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            geometry_path = Path(temporary_directory) / "room_geometry.json"
            geometry_path.write_text(
                json.dumps(
                    {
                        "room": {"width_m": 2.0, "height_m": 2.0, "grid_cell_m": 0.5},
                        "receivers": [
                            {"id": "pc_1", "label": "Laptop", "x_m": 1.0, "y_m": 1.0, "z_m": 1.0}
                        ],
                        "radios": [],
                    }
                ),
                encoding="utf-8",
            )

            geometry = load_room_geometry(geometry_path)

            self.assertEqual(2.0, geometry.room.width_m)


if __name__ == "__main__":
    unittest.main()
