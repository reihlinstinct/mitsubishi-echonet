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

The image also carries `ac_smart.py`; the entrypoint routes these subcommands
to it (the CLI's own `status` stays ECHONET-based, so the /smart one is named
`smart-status`):

```bash
docker run --rm --network host mitsubishi-echonet smart-status --ip 192.168.1.50
docker run --rm --network host mitsubishi-echonet on --ip 192.168.1.50
docker run --rm --network host mitsubishi-echonet set --ip 192.168.1.50 --mode cool --temp 24
docker run --rm --network host -v ./data:/data mitsubishi-echonet monitor --ip 192.168.1.50 --out /data/energy.csv
docker run --rm --network host mitsubishi-echonet echonet-test --ip 192.168.1.50
```

`--network host` is required on Linux so the UDP 3610 probe (and multicast
discovery) reaches the adapter; /smart itself is plain HTTP and also works
with published ports. Works identically with `podman`. The image is
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

Every subcommand takes `--ip` (required except on `discover`) and `--timeout`;
the CLI also has `--version` (print the version and exit).

Don't want to install anything? The enable/disable steps are single curl
commands - see [docs/guide-he.md](docs/guide-he.md) (Hebrew) or the source of
`src/mitsubishi_echonet/smart.py` (payload constants with provenance).

## Control the AC directly (no ECHONET needed)

`scripts/ac_smart.py` is a single stdlib-only file (Python 3.10+, nothing to
install) that talks to the adapter's local `/smart` API. Global options
(`--ip`, `--timeout`) work before or after the subcommand:

```bash
python3 scripts/ac_smart.py --ip 192.168.1.50 status       # read-only: full state
python3 scripts/ac_smart.py --ip 192.168.1.50 on           # power on (keeps last mode/temp)
python3 scripts/ac_smart.py --ip 192.168.1.50 off          # power off

# set reads the current state first and changes only what you pass:
python3 scripts/ac_smart.py set --ip 192.168.1.50 --mode cool --temp 24 --fan auto
python3 scripts/ac_smart.py set --ip 192.168.1.50 --vane-v swing --vane-h center
python3 scripts/ac_smart.py set --ip 192.168.1.50 --dehum 50 --power-saving on
python3 scripts/ac_smart.py set --ip 192.168.1.50 --remote-lock power,temp   # or: unlock
python3 scripts/ac_smart.py set --ip 192.168.1.50 --buzzer   # beep the indoor unit

# monitor polls power/energy and appends CSV (or JSON lines with a .json/.jsonl name;
# --format csv|json overrides the extension default):
python3 scripts/ac_smart.py monitor --ip 192.168.1.50 --out energy.csv --interval 30

python3 scripts/ac_smart.py --ip 192.168.1.50 echonet-test # is ECHONET answering on UDP 3610?
```

`status` shows the full state the adapter reports: power, mode, set temp,
fan, vanes, i-See, room/outdoor temps, compressor runtime, error codes,
energy (current W, lifetime kWh), and adapter info. If every energy field
reads zero, `status` says so explicitly: some units update them only while
the compressor runs, and some adapters never report cumulative energy.

`set` covers the controllable surface of the protocol: mode
(auto/heat/dry/cool/fan), setpoint (16.0-31.0 C in 0.5 steps), fan
(auto/s1-s4/full), both vanes, dehumidifier level, power-saving, remote lock
and the buzzer. It sends frames byte-identical to pymitsubishi's control
commands (the same changeset flow: read current state, change only the
requested fields). Not controllable over /smart: the i-See sensor flag and
the wide-vane adjustment bit are read-only in the protocol library, and
timers/schedules stay in the MELCloud app.

`monitor` writes one row per poll: timestamp, compressor running,
instantaneous W, cumulative kWh, power, mode, set temp, room temp. Minimum
interval is 10 s (default 30) - the adapter is a small device, poll gently.
Stop with Ctrl-C or `--count N`. A long monitor run doubles as the answer to
"does my unit report energy?": if W and kWh stay 0 even while the compressor
runs, this unit/adapter does not report them.

The on/off frame carries only the power bit - mode, temperature and fan are
left as the AC has them. Verified byte-identical to pymitsubishi's control
frames; `/smart` is unauthenticated, so trusted networks only.

## Troubleshooting: ECHONET enabled but no UDP replies

Field notes from real use: the `/smart` HTTP path (enable/disable here, and
`scripts/ac_smart.py` for status/power) is the verified-working path on a
live MAC-577IF-E (app 40.00) - after `enable`, the adapter's own status
reply confirms `ECHONET: ON`. If ECHONET is on but UDP 3610 stays silent:

- **Bind port 3610.** These adapters send replies to fixed UDP port 3610,
  not to your request's source port. A client on an ephemeral port hears
  nothing; bind 3610 (`scripts/ac_smart.py echonet-test` does this).
- **Check what arrives.** `sudo tcpdump -i any -n udp port 3610` while
  probing: a visible reply that the tool does not see means a host firewall
  (`sudo ufw allow 3610/udp`); no reply at all means the network drops it.
- **Client isolation.** Guest Wi-Fi and some mesh routers block UDP between
  clients. Everything must be on the same non-guest network.

If you hit this and solve it differently, please open an issue.

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
