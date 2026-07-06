<p align="center">
<img src="https://user-images.githubusercontent.com/51358498/152991504-005a1daa-2900-4f48-8bec-d163d6336ed2.png" width="400">
</p>

# OpenWeedLocator (OWL)

**Open-source, low-cost weed detection for site-specific weed control**

[![Tests](https://github.com/geezacoleman/OpenWeedLocator/actions/workflows/tests.yml/badge.svg)](https://github.com/geezacoleman/OpenWeedLocator/actions/workflows/tests.yml)
[![DOI](https://zenodo.org/badge/399194159.svg)](https://zenodo.org/badge/latestdoi/399194159)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/downloads/)

OWL is a camera-based weed detection system based on the Raspberry Pi that uses green-detection algorithms to trigger relay-controlled solenoids for spot spraying. It's built entirely from off-the-shelf components and 3D-printable parts, making precision weed control accessible to anyone.

[**Website**](https://www.openweedlocator.org) | [**Documentation**](https://docs.openweedlocator.org) | [**Community**](https://community.openweedlocator.org) | [**Newsletter**](https://openagtech.beehiiv.com/)


### OWLs in Action

<table>
  <tr>
    <th>2m vehicle OWL</th>
    <th>2m robot-mounted OWL</th>
    <th>Bicycle OWL</th>
  </tr>
  <tr>
    <td align="center">
      <img src="https://user-images.githubusercontent.com/51358498/130522810-bb19e6ca-5019-4de4-83cc-858eca358ef8.jpg" width="250"/>
    </td>
    <td align="center">
      <img src="https://github.com/geezacoleman/OpenWeedLocator/assets/51358498/9cb73514-dffc-4c53-969e-c1c816610f1b" width="250"/>
    </td>
    <td align="center">
      <img src="https://github.com/geezacoleman/OpenWeedLocator/assets/51358498/17ad4ead-429e-4384-9e74-b050a536897f" width="250"/>
    </td>
  </tr>

  <tr>
    <th>12m X-fold OWL (in development)</th>
    <th>4m OWL sprayer</th>
    <th>16 channel vegetables OWL</th>
  </tr>
  <tr>
    <td align="center">
      <img src="https://github.com/user-attachments/assets/c39308cf-ccd7-4428-a10f-fef2fa0ae5af" width="250"/>
    </td>
    <td align="center">
      <img src="https://github.com/user-attachments/assets/f32c307d-7fa4-4907-82f2-53519e2236b8" width="250"/>
    </td>
    <td align="center">
      <img src="https://github.com/user-attachments/assets/b8c8027a-bacd-41e3-9595-8786ff16b715" width="250"/>
    </td>
  </tr>
</table>

---

## Quick Start

```bash
# Clone and install on Raspberry Pi (Bookworm or Trixie OS)
git clone https://github.com/geezacoleman/OpenWeedLocator owl
bash owl/owl_setup.sh
```
During this process you'll be asked to setup:
1. Green-on-Green - this adds about 2GB of dependencies
2. Dashboard - standlone or networked

One complete you'll need to reboot and then it should be running.

To confirm, run `sudo systemctl status owl.service` or `sudo journalctl -u owl.service -f`

See the [two step installation guide](https://docs.openweedlocator.org/en/latest/software/index.html) for detailed instructions.

## Rust-Spray detector backend

OWL can optionally run [Rust-Spray](https://github.com/cropcrusaders/Rust-Spray) as a
high-performance detection inner loop (`algorithm = rustspray` in `GENERAL_CONFIG.ini` —
see [config/README.md](config/README.md#rustspray)). OWL's Python keeps frame capture,
scheduling, logging and the dashboard; the Rust binary does SIMD pixel classification and
lane reduction, fed raw RGB24 frames over stdin (IPC protocol v1). If the subprocess ever
crashes or misses its frame deadline, OWL falls back to the Python `exg` detector
automatically.

### Lag comparison: Python detectors vs Rust-Spray

Per-frame detection lag (frame in → lane/actuation decision out) on identical synthetic
field frames (textured soil, ~2% weed coverage), 300 frames per case. The Rust-Spray
row is end-to-end as OWL experiences it: BGR→RGB conversion + pipe transfer + SIMD
detection + lane reduction + JSON response; *rust-internal* is the detection time the
binary reports for the same frames, showing that transport dominates the remaining lag.

| Resolution | Detector | mean ms | median ms | p95 ms | rust-internal ms |
|---|---|---|---|---|---|
| 416x320 | python `exg` | 1.76 | 1.72 | 2.07 | — |
| 416x320 | python `exhsv` (default) | 2.39 | 2.36 | 2.54 | — |
| 416x320 | **rustspray (end-to-end IPC)** | **1.15** | **1.19** | **1.31** | 0.24 |
| 640x480 | python `exg` | 3.75 | 3.74 | 3.86 | — |
| 640x480 | python `exhsv` (default) | 5.13 | 5.10 | 5.44 | — |
| 640x480 | **rustspray (end-to-end IPC)** | **1.32** | **1.26** | **1.62** | 0.56 |
| 1280x720 | python `exg` | 11.18 | 11.13 | 11.74 | — |
| 1280x720 | python `exhsv` (default) | 15.55 | 15.32 | 16.61 | — |
| 1280x720 | **rustspray (end-to-end IPC)** | **6.58** | **6.06** | **7.99** | 1.70 |
| 1456x1088 | python `exg` | 15.55 | 15.51 | 16.57 | — |
| 1456x1088 | python `exhsv` (default) | 23.46 | 23.44 | 24.88 | — |
| 1456x1088 | **rustspray (end-to-end IPC)** | **11.76** | **10.70** | **15.38** | 3.07 |

At 30 km/h the boom travels 8.3 mm per millisecond, so at OWL's default capture
resolution (1456x1088) the mean detection lag corresponds to ~19.5 cm of travel with
`exhsv` versus ~9.8 cm with the Rust-Spray backend; at 640x480 it is ~4.3 cm versus
~1.1 cm.

**Caveats:** measured on an x86_64 dev container, not a Raspberry Pi — absolute numbers
will be higher on a Pi, but both paths run on the same CPU so the *relative* difference
is what matters. This is detection lag only, not full camera-to-nozzle latency (exposure,
capture, and relay switching are identical for both backends and add on top). Reproduce
with:

```bash
python benchmarks/bench_rustspray_lag.py --binary /usr/local/bin/rustspray --markdown
```

## Documentation

- [**Getting Started**](https://docs.openweedlocator.org/en/latest/getting-started/index.html) - What you need, how it works
- [**Hardware Assembly**](https://docs.openweedlocator.org/en/latest/hardware/index.html) - Parts list, wiring, 3D printing
- [**Software Setup**](https://docs.openweedlocator.org/en/latest/hardware/index.html) - Installation, configuration, algorithms
- [**Controller Setup**](https://docs.openweedlocator.org/en/latest/controllers/index.html) - Standalone and networked dashboards
- [**Troubleshooting**](https://docs.openweedlocator.org/en/latest/troubleshooting/index.html) - Common issues and fixes

### Contributing

Contributions are welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines, or join the conversation at [community.openweedlocator.org](https://community.openweedlocator.org).

## Citing OWL

OpenWeedLocator was originally published in [Scientific Reports](https://www.nature.com/articles/s41598-021-03858-9). 
```
@article{Coleman2022,
author = {Coleman, Guy and Salter, William and Walsh, Michael},
doi = {10.1038/s41598-021-03858-9},
issn = {2045-2322},
journal = {Scientific Reports},
number = {1},
pages = {170},
title = {{OpenWeedLocator (OWL): an open-source, low-cost device for fallow weed detection}},
url = {https://doi.org/10.1038/s41598-021-03858-9},
volume = {12},
year = {2022}
}

```
The OWL speed testing paper has been published in [Computers and Electronics in Agriculture](https://www.sciencedirect.com/science/article/pii/S0168169923008074). Please
consider citing the published article using the details below.
```
@article{Coleman2023,
author = {Coleman, Guy R.Y. and Macintyre, Angus and Walsh, Michael J. and Salter, William T.},
doi = {10.1016/j.compag.2023.108419},
issn = {0168-1699},
journal = {Computers and Electronics in Agriculture},
pages = {108419},
title = {{Investigating image-based fallow weed detection performance on Raphanus sativus and Avena sativa at speeds up to 30 km h$^{-1}$}},
url = {https://doi.org/10.1016/j.compag.2023.108419},
volume = {215},
year = {2023}
}
```

### License

This project is licensed under the [MIT License](LICENSE).

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=geezacoleman/OpenWeedLocator&type=Timeline)](https://star-history.com/#geezacoleman/OpenWeedLocator&Timeline)
