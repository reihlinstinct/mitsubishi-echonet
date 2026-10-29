# mitsubishi-echonet

[![CI](https://github.com/reihlinstinct/mitsubishi-echonet/actions/workflows/ci.yml/badge.svg)](https://github.com/reihlinstinct/mitsubishi-echonet/actions/workflows/ci.yml)

Every workflow ends with a `Run summary` job that writes a summary to the top of the run page: which jobs ran, their result and duration, what was skipped and why, the commits and files the run changed, and which checks cover those changes (`.github/ci-coverage-map.json` declares the coverage; files matching no mapping are flagged). The script is `scripts/ci/run_summary.py`.
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Enable, disable and verify **local ECHONET Lite control** on Mitsubishi Electric
Wi-Fi adapters (MAC-577IF-E, MAC-577IF2-E, MAC-587IF-E and relatives) that ship
with ECHONET Lite compiled in but switched off outside Japan and AU/NZ.

Once enabled, the air conditioner is fully controllable on the home network over
UDP 3610 - no cloud - through anything that speaks ECHONET Lite: Home Assistant
(`echonetlite_homeassistant`), Homebridge (`homebridge-echonet-hvac`, for Apple
HomeKit), and others. MELCloud / MELCloud Home keeps working in parallel.

Hebrew step-by-step guide: the [wiki](https://github.com/reihlinstinct/mitsubishi-echonet/wiki) (source: [docs/wiki/he-ac-local-control.md](docs/wiki/he-ac-local-control.md), PR-reviewed).
Design and protocol notes: [docs/DESIGN.md](docs/DESIGN.md).
Scheduling research (Shabbat clock, timers): [docs/DESIGN-scheduler.md](docs/DESIGN-scheduler.md).

## Safety and fair warning

- **Unofficial.** The enable command uses the adapter's unauthenticated local
  `/smart` service endpoint with the factory-default command key. Mitsubishi
  could change this in a firmware update. If that happens, re-run `enable` or
  see the design doc.
- **ECHONET Lite has no authentication and no encryption.** Any device that can
  reach UDP 3610 can control the AC. Only do this on a trusted home network.
- **Whole degrees only.** ECHONET Lite sets temperature in whole °C.
- The adapter tells MELCloud that ECHONET was enabled. MELCloud keeps working;
  blocking the adapter from the internet stops that (and stops MELCloud).

## Install

Requires Python 3.10+ and nothing else (standard library only):

```bash
pipx install git+https://github.com/reihlinstinct/mitsubishi-echonet.git
# or: pip install git+https://github.com/reihlinstinct/mitsubishi-echonet.git
```

## Run in a container (no install on your machine)

```bash
docker build -t mitsubishi-echonet .
docker run --rm --network host mitsubishi-echonet status --ip 192.168.1.50
```

`--network host` is required on Linux so the UDP 3610 probe (and multicast
discovery) reaches the adapter. Works identically with `podman`. The image is
Alpine-based, pinned by digest, runs as a non-root user, and installs no third
party packages.

## Usage

The machine must be on the same LAN as the adapter.

```bash
# Read-only check before touching anything: reachable? ECHONET already on?
mitsubishi-echonet status --ip 192.168.1.50

# Enable local control
mitsubishi-echonet enable --ip 192.168.1.50

# Verify (adapter now answers ECHONET Lite probes on UDP 3610)
mitsubishi-echonet status --ip 192.168.1.50

# Turn it back off
mitsubishi-echonet disable --ip 192.168.1.50

# Find all ECHONET Lite devices on the network
mitsubishi-echonet discover

# Read-only: list the adapter's supported ECHONET properties (incl. timers)
mitsubishi-echonet capabilities --ip 192.168.1.50
```

Don't want to install anything? The enable/disable steps are single curl
commands - see [docs/guide-he.md](docs/guide-he.md) (Hebrew) or the source of
`src/mitsubishi_echonet/smart.py` (payload constants with provenance).

## How it works

Short version: `enable`/`disable` POST an encrypted XML service command to
`http://<adapter>/smart` (the one route that needs no password); `status` and
`discover` send ECHONET Lite GET frames to UDP 3610 and parse the replies.
Long version, with sources: [docs/DESIGN.md](docs/DESIGN.md).

## Development

```bash
pip install -e . pytest pytest-cov ruff mypy
pytest        # unit tests, 80% coverage floor
ruff check src tests
mypy src
```

## Credits

Built on open community research - full list in [docs/DESIGN.md](docs/DESIGN.md):
the `/smart` enable method (mafredri, echonetlite_homeassistant issue #226),
the underlying protocol reverse-engineering (ncaunt/meldec issue #2,
dragonbane0, niobos), and melsmart (mafredri/meltools) whose `-disable-echonet`
command our disable payload matches.

## License

MIT - see [LICENSE](LICENSE).
