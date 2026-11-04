# Read-only Mitsubishi observations for pyhems

This checklist prepares optional, read-only observations from a Mitsubishi ECHONET Lite adapter for [pyhems issue #49](https://github.com/sayurin/pyhems/issues/49). Do not change adapter settings or send ECHONET write commands as part of this investigation.

## Current contribution status

- [pyhems PR #80](https://github.com/sayurin/pyhems/pull/80) is waiting for the HEMS integration interface alignment. The maintainer asked us to wait; do not extend the change until the interface contract is ready.
- [pyhems issue #79](https://github.com/sayurin/pyhems/issues/79) is the related time/duration/date/date-time type work. It is tied to PR #80, not a separate unblocked task.
- [pyhems issue #49](https://github.com/sayurin/pyhems/issues/49) asks for more evidence about Mitsubishi Air Resort values, including tentative interpretations of EPC `0xCF` and `0x9A`.
- The [HEMS integration repository](https://github.com/sayurin/hems_echonet_lite) currently has no open issues or PRs. Its latest release is [v0.9.0](https://github.com/sayurin/hems_echonet_lite/releases/tag/v0.9.0), after [PR #134](https://github.com/sayurin/hems_echonet_lite/pull/134) adopted pyhems's refactor and [PR #135](https://github.com/sayurin/hems_echonet_lite/pull/135) updated the pyhems dependency. These recent integration changes are why we should avoid overlapping work until a concrete need is identified.

## Before collecting anything

1. Confirm the unit and ECHONET object class to be observed. The recorded home unit is a Mitsubishi MSZ-GE71VA-E1 with MAC-577IF-E adapter, but issue #49 does not state its device class/model. Do not assume the home unit is the Mitsubishi Air Resort device described in that issue.
2. Confirm the adapter is already reachable and ECHONET Lite is already enabled. On the same trusted LAN, `mitsubishi-echonet status --ip <ADAPTER_IP>` is read-only. Do not run `enable`, `disable`, `set`, or any other write command for this task.
3. Use `mitsubishi-echonet capabilities --ip <ADAPTER_IP>` to inspect the ECHONET GET property map. Only request an EPC if `0x9A` or `0xCF` appears in the GET map. Absence means the adapter does not expose it for GET; record that and stop for that EPC.

## Read-only GET procedure

The project's `src/mitsubishi_echonet/echonet.py` implements ECHONET Lite frames, `query()` and property-map reads. Its CLI does not currently expose arbitrary raw EPC reads, so a small temporary script may call those existing read helpers or construct a single-property GET. Use UDP port 3610 and bind the local socket to port 3610, as described in the [local-control guide](https://github.com/reihlinstinct/mitsubishi-echonet/blob/main/docs/wiki/he-ac-local-control.md#troubleshooting-echonet-enabled-but-no-udp-replies).

For a home air-conditioner object instance `01` (EOJ `01 30 01`), the GET requests below use controller source EOJ `05 FF 01`. Use unique transaction IDs and send one request at a time:

- EPC `0x9A`: `10 81 00 01 05 FF 01 01 30 01 62 01 9A 00`
- EPC `0xCF`: `10 81 00 02 05 FF 01 01 30 01 62 01 CF 00`

For each request, save the date/time, confirmed object class and instance, request frame, responder IP, and the entire response frame as hex. A successful GET response uses ESV `0x72` and includes the requested EPC, PDC length, and EDT bytes. Record a GET_SNA response or timeout as-is; do not treat silence as evidence of a particular value.

- For `0x9A`, collect a second read later if possible, under naturally occurring operation. Record whether the compressor was running according to the read-only `status` output. Do not turn the AC on or alter its mode for the test. Preserve the raw EDT; do not label it as seconds or infer a reference epoch without separate evidence.
- For `0xCF`, preserve the raw EDT. If a clearly labelled filter/cleaning indicator is already visible on the unit or in its app, note that observation and its time. Do not trigger cleaning, alarms, or setting changes.

## Important interpretation limits

The current pyhems MRA file for home air conditioner class `0x0130` maps EPC `0xCF` to “Air purification mode setting”; EPC `0x9A` is not present in that class file. That differs from the tentative filter-alarm/runtime interpretations mentioned in issue #49. Confirm the observed device class and model against the issue before comparing results. Raw readings from a different class or device may not resolve that issue.

A contribution could follow once the device/class is confirmed: provide raw, reproducible observations to help the issue author validate or correct EPC meanings, then propose only the smallest MRA/custom-definition/test change supported by that evidence. Do not post readings or change upstream definitions without reviewing whether they identify the owner's home or equipment.
