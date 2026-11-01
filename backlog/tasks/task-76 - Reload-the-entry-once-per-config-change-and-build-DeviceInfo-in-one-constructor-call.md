---
id: TASK-76
title: >-
  Reload the entry once per config change and build DeviceInfo in one
  constructor call
status: In Progress
assignee:
  - '@copilot'
created_date: '2026-10-31T10:28:00Z'
updated_date: '2026-11-01 18:47'
labels:
  - ha-compat
  - config-flow
  - tech-debt
milestone: m-1
dependencies: []
references:
  - custom_components/2n_intercom/__init__.py
  - custom_components/2n_intercom/config_flow.py
  - custom_components/2n_intercom/coordinator.py
  - tests/test_config_flow.py
  - tests/test_init.py
  - tests/test_coordinator.py
  - >-
    https://contributors.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/
  - 'https://contributors.home-assistant.io/blog/2026/10/13/device-info-class/'
  - 'https://github.com/home-assistant/core/pull/169198'
priority: medium
type: enhancement
ordinal: 82000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Two small Home Assistant compatibility fixes found in the HA developer blog. No user-visible change apart from one reload instead of two after reauth, reconfigure or an SSDP host change.

**1. Double reload (fact from code; user-visible effect not observed).**
`__init__.py:586` registers an update listener (`async_update_options`, `:590-594`) that reloads the entry on every entry update. The reauth flow (`config_flow.py:641-647`), the reconfigure flow (`:734-746`) and the SSDP host change (`:492-497`) call `async_update_entry(...)` and then reload again themselves. In HA core, `async_update_entry` schedules every update listener when the entry changed (`homeassistant/config_entries.py`, `_async_save_and_notify`, read on `dev` 2026-10-27), so each of these flows requests two reloads. HA says this "can cause the integration to reload twice and/or create a race condition" and deprecates the combination from 2026.6 (https://contributors.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/, checked 2026-10-26).
The 2026.12 error (core PR https://github.com/home-assistant/core/pull/169198, read 2026-10-27) is raised only inside `async_update_reload_and_abort` and `_abort_if_unique_id_configured(updates=...)`, which we do not call today, so there is no deadline. The fix below starts using `async_update_reload_and_abort`, so the update listener must be removed in the same change. `OptionsFlowWithReload` raises `ValueError` while any update listener is registered.

**2. DeviceInfo dict-style writes.**
`coordinator.py:1724-1754` (`get_device_info`) creates `DeviceInfo(...)` and then writes `configuration_url`, `serial_number`, `hw_version` and `connections` with `info["..."] = ...`. From HA 2026.11 `DeviceInfo` is a dataclass; `[]` writes still work through a compatibility layer that "will be deprecated and removed in a later version"; unset fields read as `UNDEFINED`, not `None` (https://contributors.home-assistant.io/blog/2026/10/13/device-info-class/, checked 2026-10-27).

**Current behaviour.** Reauth / reconfigure / SSDP host change: entry updated, then two reloads are requested (update listener + explicit `async_reload`). `get_device_info` mutates the `DeviceInfo` after construction.

**Expected behaviour.** Every config change requests exactly one reload through the HA-provided mechanism. `DeviceInfo` is built with all fields in one constructor call and works with both the TypedDict (HA 2026.4-2026.10) and the dataclass (HA 2026.11+) form.

Minimum HA is 2026.4.0 (`hacs.json`). `OptionsFlowWithReload`, `async_update_reload_and_abort` and `async_schedule_reload` exist in HA 2026.4.0 (`homeassistant/config_entries.py` at tag 2026.4.0, checked 2026-10-27).

Reauth/reconfigure cannot be exercised by E2E (it does not change real credentials), so the reload behaviour of those flows is unit-tested and confirmed by Jan on staging.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 The entry registers no update listener: `add_update_listener` and `async_update_options` are removed from `__init__.py`; a unit test asserts setup registers no update listener [unit]
- [ ] #2 `TwoNIntercomOptionsFlow` extends `config_entries.OptionsFlowWithReload`; a unit test asserts the base class [unit]
- [ ] #3 Successful reauth ends with `async_update_reload_and_abort(entry, data=merged)` and reason `reauth_successful`, with no separate `async_update_entry` or `async_reload` call; a unit test asserts exactly one reload request and the updated data [unit]
- [ ] #4 Successful reconfigure ends with `async_update_reload_and_abort` keeping today's data and `allow_insecure` option handling, reason `reconfigure_successful`; unit tests assert exactly one reload and that data and options equal today's values (adapt `test_reconfigure_with_valid_credentials_updates_and_aborts` and `test_reconfigure_stores_and_forwards_insecure_opt_in`) [unit]
- [ ] #5 An SSDP host change updates the entry and requests exactly one reload via `hass.config_entries.async_schedule_reload`; identity verification and logging are unchanged; a unit test asserts one reload [unit]
- [ ] #6 Reauth on an entry that is not loaded (setup failed with an auth error) still updates the entry and reloads it once; unit test with a not-loaded entry [unit]
- [ ] #7 `get_device_info` passes all fields (identifiers, name, manufacturer, model, sw_version and, when present, configuration_url, serial_number, hw_version, connections) to one `DeviceInfo(...)` call; no `info[...] =` writes on DeviceInfo remain in `custom_components/` (checked in the PR diff); existing `DeviceInfoTests` stay green [unit]
- [ ] #8 Existing E2E tests stay green on the E2E intercom: `test_00_setup` (device sw_version and serial match the device), `test_20_lifecycle::test_reload_entry`, the `test_40` options-flow tests and the `test_firmware` configuration_url check [e2e]
- [ ] #9 After the merge, the staging HA system log shows no `has an update listener` or `OptionsFlowWithReload` message for `2n_intercom` after an HA restart [tester]
- [ ] #10 On staging, Jan changes one option (e.g. scan interval) and runs one reconfigure with unchanged values: each time the entry returns to loaded, entities come back and the HA log shows one reload per action [jan]
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Remove the config-entry update listener and async_update_options from __init__.py; retain the setup-time MAC backfill without reloading and assert setup registers no update listener.
2. Use OptionsFlowWithReload for options, async_update_reload_and_abort with explicit existing success reasons for reauth/reconfigure, and async_schedule_reload after the verified SSDP host update. Preserve identity/behaviour separation, allow_insecure handling, SSDP verification/logging, dual auth and identifiers.
3. Gather present optional device fields in local kwargs and construct DeviceInfo once, preserving TypedDict and dataclass compatibility.
4. Extend lightweight HA stubs and focused tests for single reloads, updated data/options, reauth of an unloaded entry, and constructor-only DeviceInfo creation; keep existing E2E coverage unchanged. Update architecture documentation only if it describes the changed reload path.
5. Run all required Python 3.12+ local gates, record implementation/evidence notes without checking acceptance criteria or finalizing status, and commit locally on the current TASK-76 branch. Do not run E2E or push.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Approved by Jan in Telegram, 2026-10-27 (grooming 2026-10-27, alternative B; milestone v1.6.0 confirmed by Jan). Spec by Steve.

Implemented the approved HA compatibility changes without touching protected paths or release metadata. Removed the entry update listener and async_update_options; setup-time MAC backfill remains unchanged. Options use OptionsFlowWithReload, reauth/reconfigure return async_update_reload_and_abort with explicit integration success reasons, and verified SSDP host changes request async_schedule_reload once. Data/options separation, allow_insecure handling, SSDP identity checks/logging, dual auth and identifiers are preserved. DeviceInfo now receives all present fields in one constructor call. Updated the existing architecture reload-path documentation and the camera-options docstring.

Local evidence (Python 3.12.15): python3 -m unittest discover -s tests -t tests passed all 796 tests; python3 validate.py passed; python3 -m py_compile custom_components/2n_intercom/*.py passed; git diff --check passed.
AC #1 evidence: test_setup_registers_no_update_listener asserts setup never calls add_update_listener and requests no reload; integration source contains neither add_update_listener nor async_update_options.
AC #2 evidence: test_options_flow_uses_reload_base_class asserts OptionsFlowWithReload inheritance.
AC #3 evidence: test_reauth_confirm_with_valid_credentials_updates_and_aborts spies on the HA helper and asserts one update, one scheduled reload, exact credential data, preserved options and reauth_successful. test_reauth_does_not_merge_options now also exercises successful reauth.
AC #4 evidence: adapted test_reconfigure_with_valid_credentials_updates_and_aborts and test_reconfigure_stores_and_forwards_insecure_opt_in assert one helper invocation/reload and exact data/options. test_reconfigure_unchanged_values_reloads_once covers unchanged values.
AC #5 evidence: test_ssdp_updates_host_for_already_configured_device asserts one scheduled reload and preserved data/options/identity/logging; existing concurrent-announcement and failed-verification tests passed.
AC #6 evidence: test_reauth_reloads_entry_after_auth_failed_setup uses a setup_error entry with no runtime data and asserts updated credentials plus one helper invocation and scheduled reload.
AC #7 evidence: existing DeviceInfoTests passed; test_device_info_constructs_all_fields_once_for_both_ha_shapes checks exact constructor kwargs for dict and immutable dataclass stubs, including optional-field omission/UNDEFINED defaults. No info[...] writes remain in custom_components.
AC #8 pending: existing test_00_setup, test_20_lifecycle::test_reload_entry, test_40_options_flow and test_firmware coverage is unchanged; no E2E/network-device commands were run on the VM. The orchestrator must obtain e2e-pi evidence.
AC #9 and AC #10 remain manual post-merge staging checks for the tester and Jan. Acceptance criteria remain unchecked and status remains In Progress for orchestrator finalization.
<!-- SECTION:NOTES:END -->
