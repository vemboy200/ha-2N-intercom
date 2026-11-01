---
id: TASK-77
title: >-
  Doorbell event: add a button attribute with the pressed call button number
  (first slice of DRAFT-1)
status: To Do
assignee:
  - '@copilot'
created_date: '2026-11-01T19:26:00Z'
updated_date: '2026-11-01T19:26:00Z'
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
  - https://github.com/mastalir1980/ha-2N-intercom/issues/10
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
1. `coordinator.py`, state: add `self._session_buttons: dict[str, int]` (session id -> button number) and `self._last_ring_button: int | None = None` next to `_last_called_peer` / `_called_peers` (`__init__`, around the `_called_peers` declaration). Add a property `last_ring_button` next to `called_peer`.
2. `coordinator.py`, parsing: add a small static helper that turns a value into a button number: `info` must be a string of 1-3 digits after `strip()`, value 1..150 (the 2N manual allows `%1`..`%150` for KeyPressed; keep the existing `_CALL_BUTTON_KEY` behaviour unchanged); anything else gives `None`. Do not treat an arbitrary string as a button.
3. `coordinator.py`, call events (`_process_log_event`, the `CallStateChanged` / `CallSessionStateChanged` branch):
   - for `CallSessionStateChanged` with `originator == "button"` and a valid `info`, store `self._session_buttons[session_id] = number` (only when `session_id` is not None);
   - where a ring is counted (`if not self._ring_detected:` ... `self._ring_count += 1`), set `self._last_ring_button = self._session_buttons.get(peer_session_id)` before the counter is exposed, so a ring that is counted on the `CallStateChanged` event still gets the button from the earlier `initialized` session event, and a ring without a stored button sets `None`;
   - pop `self._session_buttons[peer_session_id]` where the code already pops `_called_peers` on a terminal session state (end of the branch), so the dict cannot grow;
   - add `button=%r` (the stored number or `None`) to the final debug line of the call-event branch only. Do not change the other debug lines. Never log keypad keys.
4. `coordinator.py`, `_process_key_pressed_event`: after the `_CALL_BUTTON_KEY` match, `self._last_ring_button = int(key[1:])` in the same place where `_last_called_peer` is set to `None` and the ring is counted. The existing guard (`if self._ring_detected: return False`) stays first, so a ring already counted by call events keeps its button and is not changed by the later `KeyPressed`.
5. `event.py`, `_handle_coordinator_update`: add `if self.coordinator.last_ring_button is not None: attributes["button"] = self.coordinator.last_ring_button`. Do not add an event type, an entity, a translation key or a config option (no change in `strings.json`, `translations/*`, `icons.json`).
6. Tests (unit stubs only, no real HA): 
   - `tests/test_coordinator.py`: replay the captured press sequence (see the existing `originator: button` sequence near the TASK-29 capture) with `info="1"`, then with `info="2"` and `"12"`; a press without `info`; non-numeric and out-of-range `info` (`"abc"`, `"0"`, `"151"`, `""`); an incoming call (`originator=incoming`, no ring); a second press without `info` after a press with `info="2"` must give no button; two sessions at once keep their own buttons; the session entry is gone after `terminated`; a ring counted on `CallStateChanged` before the session ringing event still reports the button.
   - `tests/test_coordinator_keypress.py`: `%2` rings with button 2; keypad keys still never ring; `KeyPressed` first then call events: one ring, button unchanged; call events first then `KeyPressed`: one ring, button from the session; `test_key_values_are_never_logged` stays green.
   - `tests/test_event.py`: the emitted `ring` carries `button` as an int when known and has no `button` key when unknown; existing attribute tests stay green.
7. Docs: `README.md` (doorbell event bullet near line 39 and the ring-on-keypress paragraph: add the `button` attribute, an automation example `trigger.event.attributes.button`, and the note that only button 1 has been verified on real hardware and older firmware may not send it); `docs/ARCHITECTURE.md` (the ring-detection paragraph around the `ring_on_keypress` bullet: how the button is stored per session and cleared); `docs/API_REFERENCE.md` (firmware quirks section: `info` is only on `CallSessionStateChanged`, not on `CallStateChanged`). No CHANGELOG or version change (release task only).

Must not change: ring detection rules (TASK-31: calls into the intercom never ring, `originator` must be present and not `incoming` for session rings), the `called_id` filter, `KEYPRESS_RING_HOLD`, the snapshot, the one-event-per-ring guard, the number of doorbell entities (TASK-55). One bad event must not break the subscription: invalid `info` is ignored, never raised. No keypad key value in any log line.
<!-- SECTION:PLAN:END -->

## Implementation Notes

<!-- SECTION:NOTES:BEGIN -->
Approved by Jan in Telegram, 2026-11-01. Facts: button press on fw 3.x sends originator=button and info="1" on CallSessionStateChanged only (TASK-29 capture 2026-10-13; read-only capture by the tester agent 2026-11-01). Not verified: info for buttons 2 and up, fw 2.50. Out of scope (stay in DRAFT-1): one event type or entity per button, button names, per-button filter, migration of called_id.
<!-- SECTION:NOTES:END -->
