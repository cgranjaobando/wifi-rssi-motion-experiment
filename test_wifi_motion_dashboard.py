import unittest
from pathlib import Path


DASHBOARD_PATH = Path("wifi_motion_dashboard.html")


class WifiMotionDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dashboard_html = DASHBOARD_PATH.read_text(encoding="utf-8")

    def test_dashboard_exposes_live_mode_presets(self) -> None:
        self.assertIn('id="dashboardModeSelect"', self.dashboard_html)
        self.assertIn('<option value="demo">Demo', self.dashboard_html)
        self.assertIn('<option value="reconstruction">Reconstruction', self.dashboard_html)
        self.assertIn('<option value="calibration">Calibration', self.dashboard_html)
        self.assertIn("demoBaselineScanCount = 2", self.dashboard_html)
        self.assertIn("reconstructionBaselineScanCount = 20", self.dashboard_html)
        self.assertIn("calibrationBaselineScanCount = 60", self.dashboard_html)

    def test_dashboard_live_stream_uses_selected_mode_baseline_count(self) -> None:
        self.assertIn("baseline_scans: String(getSelectedBaselineScanCount())", self.dashboard_html)

    def test_dashboard_warns_when_reconstruction_baseline_is_low(self) -> None:
        self.assertIn('id="fieldBaselineWarning"', self.dashboard_html)
        self.assertIn("minimumReconstructionBaselineScans = 10", self.dashboard_html)
        self.assertIn("updateFieldBaselineWarning", self.dashboard_html)
        self.assertIn("Use the Mode selector", self.dashboard_html)
        self.assertIn("Relearn Positions only resets the map layout", self.dashboard_html)

    def test_dashboard_relearn_positions_explains_baseline_warning_is_separate(self) -> None:
        self.assertIn("Relearned visual router positions", self.dashboard_html)
        self.assertIn("baseline warning is controlled by the Mode selector", self.dashboard_html)

    def test_dashboard_has_visual_only_router_label_controls(self) -> None:
        self.assertIn('id="openRouterLabelsButton"', self.dashboard_html)
        self.assertIn("hiddenMapRouterLabelStorageKey", self.dashboard_html)
        self.assertIn("isMapRouterLabelHidden", self.dashboard_html)
        self.assertIn("openMapSettingsModal(mapSettingsLabelMode)", self.dashboard_html)
        self.assertIn("Hide router labels and boxes only", self.dashboard_html)
        self.assertIn("Points, paths, detection, records, and reconstruction stay active", self.dashboard_html)
        self.assertIn("router-dot", self.dashboard_html)

    def test_dashboard_has_visual_only_router_visibility_controls(self) -> None:
        self.assertIn('id="openRouterVisibilityButton"', self.dashboard_html)
        self.assertIn("hiddenMapRouterStorageKey", self.dashboard_html)
        self.assertIn("isMapRouterHidden", self.dashboard_html)
        self.assertIn("openMapSettingsModal(mapSettingsRouterMode)", self.dashboard_html)
        self.assertIn("Hide router points and paths from the floorplan only", self.dashboard_html)
        self.assertIn("Detection, records, and reconstruction stay active", self.dashboard_html)
        self.assertIn("if (isMapRouterHidden(router.key))", self.dashboard_html)

    def test_dashboard_router_grid_controls_use_static_modal_snapshot(self) -> None:
        self.assertIn('id="mapSettingsModal"', self.dashboard_html)
        self.assertIn('id="mapSettingsList"', self.dashboard_html)
        self.assertIn("mapSettingsSnapshot", self.dashboard_html)
        self.assertIn("createMapSettingsSnapshot", self.dashboard_html)
        self.assertIn("renderMapSettingsModal", self.dashboard_html)

    def test_dashboard_shows_remote_receiver_status(self) -> None:
        self.assertIn("Remote Receivers", self.dashboard_html)
        self.assertIn('id="remoteReceiverStatus"', self.dashboard_html)
        self.assertIn('id="remoteReceiverList"', self.dashboard_html)
        self.assertIn('id="refreshRemoteReceiversButton"', self.dashboard_html)
        self.assertIn('remoteReceiversApiPath = "/api/receivers"', self.dashboard_html)
        self.assertIn("loadRemoteReceivers", self.dashboard_html)
        self.assertIn("renderRemoteReceivers", self.dashboard_html)
        self.assertIn("These are not merged into detection yet", self.dashboard_html)


if __name__ == "__main__":
    unittest.main()
