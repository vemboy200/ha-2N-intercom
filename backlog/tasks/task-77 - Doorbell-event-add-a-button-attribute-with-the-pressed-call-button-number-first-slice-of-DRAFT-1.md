---
id: TASK-77
title: >-
  Doorbell event: add a button attribute with the pressed call button number
  (first slice of DRAFT-1)
status: In Progress
assignee:
  - '@copilot'
created_date: '2026-11-01T19:26:00Z'
updated_date: '2026-11-01 19:39'
labels:
  - ring-detection
  - doorbell
milestone: m-1
dependencies: []
references:
  - custom_components/2n_intercom/coordinator.py
  - custom_components/2n_intercom/event.py
  - tests/test_coordinator.py
  - tests/test_coordinator_keypress.py
  - tests/test_event.py
  - README.md
  - docs/ARCHITECTURE.md
  - docs/API_REFERENCE.md
  - backlog/docs/analysis/doc-1 - Analysis-DRAFT-1-per-button-doorbell.md
  - 'https://github.com/mastalir1980/ha-2N-intercom/issues/10'
priority: medium
type: feature
ordinal: 83000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Why: an intercom with more than one call button cannot tell an automation which button was pressed. The doorbell `ring` event carries `called_peer`, `caller_name` and `caller_number`, but not the button. The only workaround is the `called_id` peer filter, which is one filter per config entry. This is the first slice of DRAFT-1 (option A in `doc-1`): one new `button` attribute on the existing `ring` event. DRAFT-1 stays as the place for the later design questions (one event type per button, button names, per-button filter).

**Current behaviour (fact from code).** `coordinator.py` `_process_log_event` reads `originator` of `CallSessionStateChanged` but never reads `info`. The button number is dropped. `_process_key_pressed_event` matches `%1`..`%54` with `_CALL_BUTTON_KEY` and throws the key away after the match. `event.py` `_handle_coordinator_update` builds the `ring` attributes only from `called_peer` and `caller_info`.

**Evidence (facts, captured read-only on the E2E intercom, fw 3.x).** A visitor press of the only button sends `CallSessionStateChanged` with `originator="button"` and `info="1"` in all four session events (`initialized`, `connecting`, `ringing`, `terminated`). `CallStateChanged` has no `info` (its keys are `call`, `sipAccount`, `sipAccountType`, `sipCallId`, `startTime`, `uuid`, plus state, direction, session and peer). The session events have no other keys. First capture: TASK-29 (2026-10-13). Second capture by the tester agent: 2026-11-01, same result. The `info` value is only ever the button number in these captures.

**Not verified (hypotheses).** (H1) `info` is `"2"`, `"3"`, ... for other buttons: only button 1 exists on the E2E intercom and nobody with a multi-button unit has been found. (H2) fw 2.50 does not send `originator`/`info`, so the ring still works and only the button is missing. (H3) Keypad, display directory and API calls use other `originator` values without a button number. The code must therefore treat `button` as optional and never depend on it for ring detection.

**Expected behaviour.**
- When a ring is detected and its button is known, the `ring` event has the attribute `button` (an integer, for example `2`). When the button is unknown, the attribute is absent, exactly like `called_peer` today.
- The button comes from `info` of the `CallSessionStateChanged` events of the same session when `originator` is `button` and `info` is a plain number. With `ring_on_keypress` on, a `KeyPressed` call-button key `%n` gives button `n`.
- A ring never reports the button of an earlier press (stale values are cleared).
- Ring detection, the `called_id` filter, the snapshot and the number of entities do not change.
- The README states that the button number is read from the intercom event and that only button 1 has been verified on real hardware.
<!-- SECTION:DESCRIPTION:END -->

## Acceptance Criteria
<!-- AC:BEGIN -->
- [ ] #1 A press sequence with `originator=button` and `info="1"` on `CallSessionStateChanged` makes the emitted `ring` event carry `button` = 1 (int); the same sequence with `info="2"` and `info="12"` gives 2 and 12; unit tests replay the captured event order [unit]
- [ ] #2 A ring that is counted on the `CallStateChanged` ringing event, before the session ringing event, still reports the button stored from the earlier `initialized` session event of the same session [unit]
- [ ] #3 Without a valid button the `ring` event has no `button` attribute and is otherwise unchanged: no `info`, empty, non-numeric (`"abc"`) and out-of-range (`"0"`, `"151"`) values are ignored without an error, and a call into the intercom (`originator=incoming`) still never rings [unit]
- [ ] #4 A ring never reports the button of an earlier press: a second press without `info` after a press with `info="2"` has no `button`; two sessions at the same time keep their own buttons; the stored session button is removed on the terminal session state [unit]
- [ ] #5 With `ring_on_keypress` on, `KeyPressed` key `%2` rings with `button` = 2; keypad keys still never ring; when call events and `KeyPressed` of one press arrive in either order there is one ring and the button is unchanged by the second event [unit]
- [ ] #6 The doorbell event entity adds `button` to the `ring` attributes only when the coordinator has one, next to `called_peer`, `caller_name` and `caller_number`; no new entity, event type, option or translation key is added (checked in the PR diff) [unit]
- [ ] #7 The debug line of the call-event branch includes `button`; no keypad key value is logged (`test_key_values_are_never_logged` stays green); `called_id` filter tests and the TASK-31 ring tests stay green unchanged [unit]
- [ ] #8 README.md, docs/ARCHITECTURE.md and docs/API_REFERENCE.md describe the `button` attribute and state that only button 1 has been verified on real hardware (checked in the PR diff) [unit]
- [ ] #9 The existing E2E doorbell entity checks stay green on the E2E intercom (`test_10_entities::test_doorbell_event_is_available_and_shares_camera_device`); the unit and E2E suites and `validate.py` pass [e2e]
- [ ] #10 After the merge, the staging HA system log shows no error from `2n_intercom` about the log subscription or the doorbell event entity after a restart [tester]
- [ ] #11 On the real intercom Jan presses the call button once: the doorbell event in Home Assistant shows the attribute `button: 1`, also with `ring_on_keypress` on; a second press shows it again and a call into the intercom from My2N does not ring [jan]
<!-- AC:END -->

## Implementation Plan

<!-- SECTION:PLAN:BEGIN -->
1. Add optional per-session button storage and last_ring_button, parse only plain numeric session info in range 1..150, and clear session storage on terminal states. Preserve ring detection, called_id filtering, keypress matching and deduplication; set button only when a new ring is counted and include it in the existing call-event debug line. 2. Expose the optional integer button attribute on the existing doorbell ring event without new entities, options, event types or translations. 3. Extend coordinator, keypress and event unit tests for captured event ordering, invalid/missing values, isolation, cleanup and both keypress/call orders; retain the existing read-only E2E doorbell entity check required by AC #9. 4. Update README.md, docs/ARCHITECTURE.md and docs/API_REFERENCE.md with the optional attribute, automation example and real-hardware limitation. 5. Run the full Python unit suite, validate.py and integration py_compile, record implementation evidence in notes, and commit locally without checking criteria or finalizing the task. E2E and manual checks remain with the orchestrator, tester and Jan.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Approved by Jan in Telegram, 2026-11-01. Facts: button press on fw 3.x sends originator=button and info="1" on CallSessionStateChanged only (TASK-29 capture 2026-10-13; read-only capture by the tester agent 2026-11-01). Not verified: info for buttons 2 and up, fw 2.50. Out of scope (stay in DRAFT-1): one event type or entity per button, button names, per-button filter, migration of called_id.

Implemented optional session button metadata and last_ring_button. Only plain numeric string info from originator=button session events is accepted (trimmed, 1-3 ASCII digits, 1..150); invalid or missing values remain optional and never affect ring detection. A counted ring takes its own session button or None, terminal session states remove stored buttons, and accepted KeyPressed call buttons provide their integer number only when counting a new ring. The existing call-event debug line now includes the stored button. No ring rules, called_id filtering, key matching/hold, snapshots, entities, event types, options, translations, version or CHANGELOG were changed.

Unit evidence: SessionButtonTests and DoorbellEventTests replay the TASK-29 captured order for 1, 2 and 12, including the earlier initialized event and CallStateChanged ringing before session ringing. Missing/invalid info, wrong originators, non-string values, sessionless metadata, trimmed/range-boundary values, a second press without info, concurrent session isolation and every terminal state are covered. KeyPressedRingTests and DoorbellEventTests cover %2, both keypress/call event orders, stable metadata after a duplicate event and preserving one ring/snapshot. Existing TASK-31 and called_id tests remain unchanged; test_key_values_are_never_logged remains unchanged and passes.

README.md, docs/ARCHITECTURE.md and docs/API_REFERENCE.md describe the optional integer attribute, session storage/cleanup, info being absent from CallStateChanged, and only button 1 being verified on real hardware. The README automation uses the correct HA event-entity state-trigger path trigger.to_state.attributes.button (matching the existing blueprint), and explicitly distinguishes it from the plan wording trigger.event.attributes.button.

Local gates passed with Python 3.12.15: python3 -m unittest discover -s tests -t tests (811 tests), python3 validate.py and python3 -m py_compile custom_components/2n_intercom/*.py. The full suite still emits warnings from existing API failure-test mocks outside this task; those files were not changed.

AC #9: the existing read-only E2E test_10_entities::test_doorbell_event_is_available_and_shares_camera_device is retained unchanged, as requested by the task. E2E was not run from the VM; e2e-pi must provide the remaining evidence. New button metadata is proven locally by unit replay; a real press is intentionally the manual AC #11, not an automated device action. AC #10 remains with the tester after merge, and AC #11 remains with Jan. No acceptance criteria were checked and the task remains In Progress for independent review and orchestration.
<!-- SECTION:NOTES:END -->
