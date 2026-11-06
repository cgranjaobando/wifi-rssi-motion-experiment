# WiFi RSSI Motion Experiment

A Windows-based experiment for observing WiFi signal-strength changes from visible access points and visualizing possible motion around a laptop or desktop WiFi receiver.

The project samples nearby WiFi access points with `netsh`, builds a short baseline, detects signal percentage changes, writes CSV logs, and serves a live dashboard with a floorplan-style signal map.

## What This Is

- A practical RSSI-based motion experiment using a normal Windows WiFi adapter.
- A live dashboard that shows this PC as the receiver and nearby routers as signal sources.
- A baseline-and-delta detector for visible WiFi networks.
- A local-only tool that does not require router firmware changes.

## What This Is Not

- It is not true WiFi radar or CSI-based sensing.
- It does not calculate accurate human location.
- It does not measure real router distances automatically.
- It does not read private traffic or connect to other routers.

RSSI changes can suggest that the radio environment changed. They cannot prove that a person moved along an exact line on the map.

## Features

- Monitor one target access point by SSID and BSSID.
- Record all visible WiFi access points to CSV.
- Detect possible motion from RSSI deltas against a baseline.
- Write normalized JSONL sensing events for downstream integrations.
- Reconstruct a coarse RSSI/Fresnel disturbance field when room geometry is configured.
- Run a synthetic room simulator to validate the reconstruction math without live WiFi noise.
- Optionally adapt baselines during stable scans to reduce drift.
- Exclude selected router SSIDs from detection.
- Refresh Windows WiFi scans through the WLAN API.
- Serve a live Server-Sent Events API at `/api/live`.
- Accept consented remote Windows receiver samples at `/api/receiver-samples`.
- Show enrolled remote receiver health at `/api/receivers`.
- Delete live dashboard record files from the dashboard.
- Display a live dashboard with:
  - PC receiver marker
  - router aliases `R1` through `R6`
  - persistent router map positions unless you reset or drag them
  - editable detection filters for excluded SSIDs
  - visual-only map-header router label and router visibility modal controls
  - colored signal paths
  - reconstructed disturbance-field overlay when geometry is available
  - estimated disturbance heatmap over changed signal paths as a fallback
  - path estimate markers for changed router links
  - motion score
  - changed router list
  - timeline and replay support
  - live mode presets for quick demo, reconstruction, and calibration runs
  - remote receiver status for enrolled extra devices

## Requirements

- Windows
- Python 3.11 or newer
- A working WiFi adapter
- A browser for the dashboard

No third-party Python packages are required.

## Quick Start

From the project folder:

```powershell
cd "C:\WiFi Experiment"
python wifi_motion_live_server.py --host 127.0.0.1 --port 8877 --directory .
```

Open:

```text
http://127.0.0.1:8877/wifi_motion_dashboard.html
```

Do not use `python -m http.server` for live mode. A static server can load the HTML file, but it cannot serve `/api/live`, so the dashboard will fall back to replay mode.

## Verify The Live API

```powershell
curl.exe http://127.0.0.1:8877/api/status
```

Expected response:

```json
{"service": "wifi-motion-live", "live_api": true, "remote_receiver_api": true, "remote_receiver_count": 0}
```

You can also probe the live stream:

```powershell
curl.exe -N "http://127.0.0.1:8877/api/live?baseline_scans=1&threshold=2&interval=0.5&settle=0.35&output=live_probe_samples.csv&refresh=1"
```

Expected events include `baseline`, `ready`, and `snapshot`.

To include every visible SSID, pass an explicit empty exclusion:

```powershell
curl.exe -N "http://127.0.0.1:8877/api/live?baseline_scans=1&threshold=2&interval=1&settle=1&output=live_probe_samples.csv&exclude=&refresh=1"
```

## Remote WiFi Receivers

Extra WiFi receivers are the best upgrade path for this project, but each receiver must run a consented collector and send its own RSSI metadata to the central server. Do not try to read another router's clients or traffic payloads.

To let other devices on your LAN reach the dashboard server, start it on all local interfaces from this PC:

```powershell
python wifi_motion_live_server.py --host 0.0.0.0 --port 8877 --directory .
```

Then open the dashboard locally at:

```text
http://127.0.0.1:8877/wifi_motion_dashboard.html
```

On another owned Windows laptop or desktop, copy this project folder or at least `wifi_receiver_agent.py`, `wifi_remote_receivers.py`, and `wifi_signal_monitor.py`, then run:

```powershell
python wifi_receiver_agent.py --server-url http://192.168.40.3:8877 --receiver-id dell_192_168_40_4 --receiver-label "Dell receiver" --gateway-address 192.168.40.1 --interval 1
```

Check receiver health from the main PC:

```powershell
curl.exe http://127.0.0.1:8877/api/receivers
```

The dashboard also has a **Remote Receivers** panel. A device appears there only after it runs a collector and posts RSSI batches to `/api/receiver-samples`. These remote readings are shown as enrollment/health data first; they are not merged into motion detection until receiver-aware baselines are added.

If there are many devices, generate a safe enrollment plan from the current LAN table:

```powershell
python wifi_receiver_enrollment.py --server-url http://192.168.40.3:8877 --gateway-address 192.168.40.1
```

This prints candidate IPs, a conservative classification, and the receiver-agent command to run on each owned Windows device. It does not remotely install or start anything. Fully automatic deployment requires an explicit management channel that you control, such as PowerShell Remoting for Windows devices, SSH for Linux/Raspberry Pi devices, ADB or an Android app for phones, or an MDM tool.

This first receiver layer is intentionally separate from `/api/live` detection. It confirms that extra devices can produce stable RSSI batches before the project adds receiver-aware baselines and reconstruction. That avoids mixing two receivers that see the same BSSID at different strengths into one incorrect baseline.

### Connecting Devices From Another Router

Use this decision path:

1. If the other router is an extender or bridge on the same network, the device can usually reach this PC at `http://192.168.40.3:8877`. Run the receiver agent with that server URL.
2. If the other router creates a different subnet, for example `192.168.1.x`, the device may not reach `192.168.40.3` directly. Use one of these controlled paths:
   - connect the device to the same WiFi/LAN as this PC during the experiment
   - configure your routers so the networks can route to each other
   - run a separate central server on that network and later import the data
   - use a private VPN/mesh such as Tailscale or ZeroTier between your own devices
3. If the router is a guest network or client-isolated network, remote receivers usually cannot contact the PC. Disable isolation only on your own router if you intentionally want those devices enrolled.

The quick reachability test from the other device is:

```powershell
curl.exe http://192.168.40.3:8877/api/status
```

If that returns JSON, the device can send receiver samples to this PC. If it times out, it is on a separated network path and needs one of the routing options above.

Current candidate handling:

| Candidate | Classification | Next step |
| --- | --- | --- |
| `192.168.40.4` Dell | Best Windows receiver candidate | Run `wifi_receiver_agent.py` on that device if it is yours. |
| `192.168.40.9` Samsung | Possible receiver only if it can run a collector | Android needs a separate collector because this Windows agent uses `netsh`. TVs/appliances are not useful as receivers. |
| Stale/private-MAC LAN entries | Unverified | Ignore until the device is awake, owned, and intentionally enrolled. |
| Visible routers/APs | Passive signal sources | Use their RSSI as transmitter links only; do not access admin pages or clients without an authorized API. |

## Command Line Usage

### Record all visible routers

```powershell
python wifi_signal_monitor.py --all-visible --gateway-address 192.168.40.1 --seconds 60 --interval 1 --output visible_router_signal_samples.csv
```

### Detect motion from other routers

```powershell
python wifi_signal_monitor.py --detect-motion --gateway-address 192.168.40.1 --seconds 120 --interval 1 --output motion_samples.csv --baseline-scans 5 --signal-change-threshold 3 --adaptive-baseline
```

This writes:

- `motion_samples.csv`
- `motion_samples_motion_events.csv`
- `motion_samples_sensing_events.jsonl`

### Monitor one specific router radio

```powershell
python wifi_signal_monitor.py --network-name HomeNet --hardware-address 58:72:c9:00:00:01 --gateway-address 192.168.40.1 --seconds 60 --interval 1 --output target_router_samples.csv
```

## Dashboard Modes

The dashboard has two runtime modes:

- **Live mode**: served by `wifi_motion_live_server.py` on port `8877`; connects to `/api/live`.
- **Replay mode**: opened from `file://` or a static server; loads existing CSV files only.

In Live mode, the **Mode** selector controls how many scans are used to build the baseline before snapshots start:

| Mode | Baseline scans | Use when |
| --- | ---: | --- |
| Demo | 2 | You want the dashboard to start quickly and accept noisier readings. |
| Reconstruction | 20 | You want steadier RSSI/Fresnel field reconstruction. |
| Calibration | 60 | You are collecting a slower, more stable calibration run. |

If room geometry is configured and field reconstruction is active, the dashboard warns when the selected mode uses fewer than 10 baseline scans. Demo mode is useful for interaction testing, but Reconstruction or Calibration mode is better for field estimates.

If the browser console shows:

```text
/api/live ... 404 File not found
```

the page is running from the wrong server. Open the `8877` URL from the live server.

## How Motion Detection Works

1. The tool scans visible WiFi access points.
2. It filters out only excluded SSIDs and invalid signal records.
3. It builds a baseline average for each remaining SSID/BSSID pair.
4. New scans are compared against the baseline.
5. Signal changes above the configured threshold are treated as changed paths. Weak or saturated signals are still visible by default, but the visualization can weight them lower.
6. The changed paths are summarized into:
   - `stable`
   - `possible_movement`
   - `strong_movement`

The score is the sum of absolute signal percentage changes for the current scan.

## Room Geometry And Field Reconstruction

The project can now add a Radio Tomographic Imaging-style inference layer after the existing detector:

```text
scan -> baseline -> deltas -> link reactions -> Fresnel backprojection -> disturbance field
```

This is still RSSI-based. Windows `netsh` provides one signal percentage per visible BSSID, not CSI or a full wavefront. The reconstruction estimates which room grid cells best explain the changed PC-router signal paths. It is a probability-style field, not proof of exact human location.

### Reconstruction Methods

Two inference methods are available over the same Fresnel link model:

- **Backprojection** (default, `rssi_fresnel_backprojection_v1`): sums each changed link's normalized Fresnel weight into the grid. Fast and robust, but blurs the disturbance across overlapping links.
- **Regularized** (`rssi_tikhonov_dual_v1`): solves a Tikhonov-regularized least-squares inverse in link space (`x = Aᵀ (A Aᵀ + λI)⁻¹ y`). It de-correlates overlapping links for a sharper, less smeared estimate from the same data, and clamps the field to non-negative values.

Select the method in the dashboard with the **Reconstruction** selector, or on the live API with the `reconstruction` query parameter:

```powershell
curl.exe -N "http://127.0.0.1:8877/api/live?baseline_scans=1&threshold=2&interval=1&settle=1&reconstruction=regularized&refresh=1"
```

Compare both methods against a synthetic disturbance with the simulator, which prints peak and centroid error for each.

### Resolution Note

Spatial resolution is governed by the number of independent transmitter→receiver links and their geometric diversity, not by grid density. A finer grid (more cells) produces a smoother image but does not add real information: with one receiver and a handful of routers the inverse problem is heavily underdetermined. To genuinely sharpen reconstruction, add more receivers/links (or move to CSI-capable hardware), rather than increasing the number of grid points.

The code uses geometry and radio-physics approximations where the data supports them: Euclidean distance for configured coordinates, wavelength from `speed_of_light / frequency`, and Fresnel extra path length. It does not measure distance from wave travel time because Windows RSSI scans do not expose time-of-flight, phase, or bounce timing. That requires RTT/FTM, CSI-capable hardware, or radar-style radios.

Start from the example geometry:

```powershell
copy config\room_geometry.example.json config\room_geometry.json
```

Then edit `config\room_geometry.json` with:

- room width, height, and grid cell size in meters
- this PC receiver position
- visible router/AP SSID, BSSID, band, channel, position, and reliability

Live reconstruction appears only when the configured radio identifiers match the radios seen in live scans. If no geometry match exists, the dashboard falls back to the frontend signal-path heatmap. The local `config/room_geometry.json` file is ignored by git so private SSIDs, BSSIDs, and room coordinates are not published by accident.

Run the simulator:

```powershell
python wifi_field_simulator.py
```

It builds synthetic links, generates fake RSSI reactions from a known disturbance, reconstructs the field, and prints peak/centroid error metrics.

## Distance Estimation (RSSI Ranging)

The `wifi_ranging.py` module estimates a coarse radial distance per router link from RSSI using the classic indoor log-distance path-loss model:

```text
rssi(d) = rssi_at_1m - 10 * n * log10(d)
```

Windows reports a signal *quality* percentage, which is mapped to approximate RSSI in dBm (`0% -> -100 dBm`, `100% -> -50 dBm`) before ranging.

When room geometry is configured, the model is **calibrated by least squares** against the known router/receiver distances, so the path-loss exponent `n` and reference level reflect your actual environment instead of a textbook guess. Each estimate includes an uncertainty band derived from the calibration residual.

Calibration requires routers at **different distances** from the receiver. Routers placed symmetrically (all equidistant) provide no distance spread to fit, so the model falls back to a default indoor profile.

This is RSSI ranging, not radar. It estimates how far, with several meters of uncertainty, and cannot resolve direction, multipath bounces, or object identity.

## What This Setup Can And Cannot Measure

| Goal | Feasible with `netsh` RSSI? | What it needs |
| --- | --- | --- |
| Detect that the environment changed | Yes | Baseline + delta (implemented) |
| Coarse disturbance location | Partial | Multiple links + reconstruction (implemented) |
| Approximate distance to a router | Yes, coarse | Calibrated path-loss (implemented) |
| Direction / angle to an object | No | Multiple antennas (AoA) |
| Multipath / signal bounces | No | Per-subcarrier CSI (amplitude + phase) |
| Time-of-flight distance | No | FTM/RTT (802.11mc) capable radios |
| Object shape or identity | No | CSI + machine learning, or radar |
| Bluetooth proximity changes | Partial | Bluetooth RSSI collector plus known beacons/devices |

Spatial resolution is set by the number of independent transmitter-receiver links and their geometric diversity, not by grid density. To genuinely improve ranging, localization, and any chance of object/obstacle inference, add more receivers/links or move to CSI-capable hardware (for example an ESP32 with CSI, a Raspberry Pi running Nexmon CSI, or an Intel 5300/Atheros CSI setup). Those expose amplitude and phase per subcarrier, which is the actual wave information that bounces, obstacles, and object reconstruction require.

## Optional Sensor Inputs

Bluetooth can help as a separate coarse proximity channel if the PC can observe stable Bluetooth Low Energy advertisements or paired device signal levels. It can answer questions like "did this beacon or phone get closer/farther?" but it does not provide WiFi-style signal-path reconstruction, room shape, or wave timing.

Useful future sensor tiers:

- **Bluetooth RSSI**: coarse near/far trend from known devices or beacons.
- **WiFi RTT/FTM**: better distance anchors when the PC and access points support it.
- **Extra WiFi receivers**: the most useful upgrade for the current RTI model because more receiver paths improve geometry.
- **Camera, microphone, PIR, or USB sensors**: direct motion evidence, but with stronger privacy tradeoffs and outside the current RF-only scope.

Keep extra sensors as separate timestamped observations first. Fuse them later only after each source has its own baseline, reliability score, and privacy boundary.

Visible AP `Bss Load` station counts, when Windows reports them, are only passive ranking hints. A low and stable station count may mean a quieter AP; a high or changing count may mean a noisier baseline. Station counts do not identify your devices and do not grant permission to query a router.

## Research-Informed Extensions

The included research note, `Designing a WiFi Sensing Extension for Movement and Spatial Context.pdf`, recommends an adapter-first sensing design, separate raw-signal storage, task-specific derived events, maintained baselines, and privacy-aware retention.

The project applies the parts that fit the current Windows RSSI setup:

- Raw samples remain in CSV for inspection and replay.
- Human-readable motion events remain in `*_motion_events.csv`.
- Normalized downstream events are written to `*_sensing_events.jsonl`.
- Live `/api/live` snapshots include a `sensing_event` object.
- Live mode enables adaptive baseline updates during stable scans.
- Baselines track observed ratio, standard deviation, median absolute deviation, and radio reliability.
- The field model uses Fresnel-style weighting and non-negative normalized backprojection.
- The dashboard labels spatial context as disturbance likelihood, not exact location.

Example normalized event shape:

```json
{
  "schema_version": "wifi-sensing-event-v1",
  "measurement": {"tier": "rssi", "source": "windows_netsh_wlan", "unit": "signal_percent"},
  "derived": {"motion_status": "possible_movement", "spatial_context": "possible_presence_change"},
  "links": [
    {
      "interpretation": "router_to_receiver_signal_path_changed",
      "baseline_median_absolute_deviation": 1.0,
      "standardized_reaction": 4.0,
      "radio_reliability": 0.89
    }
  ],
  "field_reconstruction": {
    "model": "rssi_fresnel_backprojection_v1",
    "receiver_geometry": "single_receiver_fan",
    "peak": {"x_m": 2.25, "y_m": 1.75, "confidence": 0.42},
    "centroid": {"x_m": 2.4, "y_m": 1.9},
    "area_m2": 0.75,
    "uncertainty": "high_single_receiver_geometry"
  },
  "privacy": {"contains_network_identifiers": true, "review_before_sharing": true}
}
```

CSI, RTT/FTM, AoA, and beamforming-feedback collectors are not implemented here. The JSONL event contract is the intended adapter point if those collectors are added later.

## Data And Privacy

CSV output may include nearby SSIDs, BSSIDs, signal percentages, WiFi bands, channels, and gateway ping information. Treat captures as local environment data. Review or remove sample CSV files before publishing if they reveal networks you do not want to share.

JSONL sensing events also include network identifiers by default. Do not publish captures from private environments without reviewing them.

The `.gitignore` excludes local capture patterns such as `*_samples.csv`, `*_motion_events.csv`, `live_*.csv`, `*_sensing_events.jsonl`, and `experiments/raw/`. Keep public, sanitized examples under `examples/` instead of committing private raw captures.

## Testing

Run the unit tests:

```powershell
python -m unittest -v
```

Run syntax checks:

```powershell
python -m py_compile wifi_signal_monitor.py wifi_motion_live_server.py wifi_geometry.py wifi_field_model.py wifi_field_simulator.py
python -m py_compile wifi_remote_receivers.py wifi_receiver_agent.py wifi_receiver_enrollment.py
python -m unittest -v test_wifi_geometry.py test_wifi_field_model.py
```

## Project Files

- `wifi_signal_monitor.py` - CLI scanner, baseline builder, motion detector, CSV writer.
- `wifi_motion_live_server.py` - local HTTP/SSE server for the live dashboard.
- `wifi_receiver_agent.py` - Windows remote receiver agent that posts consented RSSI batches to the live server.
- `wifi_receiver_enrollment.py` - LAN enrollment planner that generates receiver-agent commands.
- `wifi_remote_receivers.py` - remote receiver payload validation and in-memory receiver registry.
- `wifi_motion_dashboard.html` - browser dashboard for live view and CSV replay.
- `wifi_geometry.py` - room, receiver, and router geometry parser.
- `wifi_field_model.py` - RSSI/Fresnel weighting and disturbance-field reconstruction.
- `wifi_field_simulator.py` - synthetic room simulation for reconstruction validation.
- `config/room_geometry.example.json` - public example geometry.
- `config/room_geometry.json` - local git-ignored geometry used by the live server.
- `experiments/labels.example.csv` - labeled calibration-run template.
- `test_wifi_signal_monitor.py` - unit tests for parsing and detection logic.
- `test_wifi_geometry.py` - unit tests for geometry parsing.
- `test_wifi_field_model.py` - unit tests for Fresnel weighting and reconstruction.
- `test_wifi_remote_receivers.py` - unit tests for remote receiver packet parsing and summaries.
- `test_wifi_receiver_enrollment.py` - unit tests for LAN receiver enrollment planning.
- `*_samples.csv` and `*_motion_events.csv` - generated or captured experiment data.

## Practical Notes

- Keep the PC in one place while building a baseline.
- Use Demo mode for quick UI checks, Reconstruction mode for normal field reconstruction, and Calibration mode when you can wait for a stronger baseline.
- Use nearby routers with stable signals for better readings.
- All visible SSIDs are included by default. Use Detection Filters only when you intentionally want to remove a router from detection.
- Use the map-header Labels button to hide distracting router labels and boxes while keeping router points and paths active.
- Use the map-header Routers button to hide router points and paths from the floorplan while keeping detection, records, and reconstruction active.
- Start with a threshold around `2` or `3`.
- Move through the room after the baseline is ready and watch which signal paths change.
- Router positions in the dashboard are fixed visual assignments unless a router has coordinates in `config/room_geometry.json`.
- Use Relearn Positions after physically moving a router if you are using visual auto-layout instead of configured room geometry. It does not rebuild the signal baseline; use the Mode selector for baseline scan depth.
- Router aliases and generated map positions are kept stable in browser storage after each router first appears.
- Use Detection Filters in the dashboard to choose excluded SSIDs.
- Delete Records removes only the live dashboard files: `live_motion_samples.csv`, `live_motion_samples_motion_events.csv`, and `live_motion_samples_sensing_events.jsonl`.
- The RTI overlay estimates disturbed field likelihood from changed PC-router links. It identifies affected signal corridors and overlap areas, not the exact human location.
- Path markers show approximate map distance to the PC and router for each changed link.

## License

Add a license before publishing if you want others to reuse the code. MIT is a common choice for small experimental tools.
