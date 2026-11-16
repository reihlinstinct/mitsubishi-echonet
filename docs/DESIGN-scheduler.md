# Scheduler design: local scheduling for the MAC-577IF-E

Status: **design research only - no scheduler is implemented yet.** This doc
records the options for running the air conditioner on a schedule (in
particular a Shabbat clock for Jerusalem: on before candle-lighting, off after
havdalah) without relying on the cloud, and what we verified vs. what is still
unverified. Written 2026-10-29.

## Requirements

- Local control preferred: keep working when the internet is down.
- Shabbat / Yom Tov aware: weekly times shift with the Hebrew calendar
  (Jerusalem candle-lighting shifts roughly 3.5 hours across the year), so
  fixed clock times are not enough for the main use case.
- Safe failure modes: a dead scheduler must never leave the AC in a surprising
  state, and must never fight a human at the remote.

## Option comparison

### 1. On-device ECHONET timer properties (no extra hardware)

The ECHONET home air conditioner class (0x0130) defines **optional** timer
properties:

| EPC  | Property                          |
|------|-----------------------------------|
| 0x90 | ON-timer reservation (set ON/OFF) |
| 0x91 | ON-timer time setting (HH:MM)     |
| 0x92 | ON-timer relative time setting    |
| 0x94 | OFF-timer reservation             |
| 0x95 | OFF-timer time setting (HH:MM)    |
| 0x96 | OFF-timer relative time setting   |

Sources: ECHONET Lite Web API dev spec v1.4.0
(echonet.jp/wp/wp-content/uploads/pdf/General/Standard/web_api/ECHONET_Lite_Web_API_Dev_Specs_v1.4.0.pdf)
and node-echonet-lite's EDT docs
(github.com/futomi/node-echonet-lite/blob/master/EDT-01.md).

Caveats:

- **UNVERIFIED on the MAC-577IF-E.** "Optional" means each device decides.
  Neither major community stack (pychonet, homebridge-echonet-hvac) implements
  these properties, a weak signal that common adapters do not expose them.
  The `capabilities` command in this repo reads the adapter's property maps
  (0x9D/0x9E/0x9F) and reports exactly which of these EPCs exist - run it once
  ECHONET Lite is enabled.
- Even if present: daily fixed-clock ON/OFF reservations only. Not weekly,
  not Hebrew-calendar aware, so not Shabbat-capable on its own.
- Persistence across a power cut is unverified.

### 2. Local /smart HTTP API (no extra hardware)

The most complete community implementation of the adapter's local /smart API
(github.com/pymitsubishi/mac-577if-e) covers power / temperature / mode / fan /
vanes / dehumidifier / power-saving / buzzer / sensors / backup-restore - **no
timer or schedule functions**. No sourced on-device scheduling this way.

### 3. MELCloud Home weekly schedules (cloud)

The MELCloud Home app has weekly schedules, but they are **cloud-executed**:
the official MELCloud Home user manual says that while the product is offline,
"Scheduled actions may not be executed as expected"
(media.crystallize.com/mitsubishi-prod_cb88776/25/11/28/1/melcloud-home-user-manual-v1-1-1.pdf).
Fixed clock times, no Hebrew calendar, dies with the internet. Zero setup, but
exactly the cloud dependency this project exists to avoid.

### 4. The AC's own IR-remote timer (no network at all)

The indoor unit's own timer, set from the physical remote. Survives Wi-Fi and
power loss of everything else, but it is a simple daily ON/OFF timer, not
programmable over Wi-Fi, and not Shabbat-aware.

### 5. Always-on local scheduler (fallback, only Shabbat-aware route)

A small service on any always-on box (the container in this repo runs
anywhere Docker/podman runs) that:

- reads a schedule file (YAML) of rules: fixed times and Hebrew-calendar
  anchors (candle-lighting / havdalah offsets) for Jerusalem via `hdate`
  (py-libhdate, local computation, GPL-3.0 - py-libhdate.readthedocs.io);
- sends ECHONET SetC frames for power / mode / temperature and **reads back**
  the property (Get on 0x80/0xB0/0xB3) to confirm - write frames must be
  implemented and tested with mocks before any real use;
- retries with backoff when the adapter is offline (power cut) and resumes
  when it returns;
- treats any frame that fails to read back as "leave the AC alone and log",
  never blindly re-fires;
- a "manual override" rule: if the AC state changes between scheduled actions
  without the scheduler doing it, a human touched it - skip the next action
  and log.

No cloud, survives internet loss, survives AC power loss (retries). Needs one
box that is always on; that box does not exist yet in this household, which is
why implementation is deferred.

## Recommendation

1. Short term: nothing to build. If fixed times suffice, MELCloud Home weekly
   schedules work today (cloud caveat), or run `capabilities` once ECHONET is
   enabled to see whether on-device ECHONET timers exist for simple daily
   on/off.
2. When an always-on box appears (a Raspberry Pi, a mini PC, a NAS that keeps
   Docker), implement option 5 in this repo: schedule file + SetC with
   readback + hdate. That is the only option that is both local and
   Shabbat-aware.
