"""Coordinator tests for the optional ring on a call-button press (TASK-39)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from test_coordinator_access import BASE_EVENTS, _CoordinatorTestCase, _FakeAPI


def _key(key: str) -> dict:
    return {"event": "KeyPressed", "params": {"key": key}}


def _call(event: str, state: str, **params) -> dict:
    return {"event": event, "params": {"state": state, "session": 135, **params}}


class KeyPressedSubscriptionTests(_CoordinatorTestCase):
    async def test_subscribes_key_pressed_when_option_on_and_listed(self) -> None:
        api = _FakeAPI(log_caps=["CallStateChanged", "KeyPressed"])
        coordinator, _hass, _ = self._make(api, ring_on_keypress=True)

        await self._run_listener_once(coordinator)

        self.assertEqual(api.subscriptions[0], ["CallStateChanged", "KeyPressed"])
        self.assertLessEqual(set(api.subscriptions[0]), set(api.log_caps))
        self.assertEqual(coordinator.subscribed_log_events, tuple(api.subscriptions[0]))

    async def test_no_key_pressed_when_option_off(self) -> None:
        api = _FakeAPI(log_caps=["CallStateChanged", "KeyPressed"])
        coordinator, _hass, _ = self._make(api)

        await self._run_listener_once(coordinator)

        self.assertEqual(api.subscriptions[0], ["CallStateChanged"])

    async def test_no_key_pressed_when_device_does_not_list_it(self) -> None:
        for caps in (["CallStateChanged"], None, [], RuntimeError("caps failed")):
            with self.subTest(caps=caps):
                api = _FakeAPI(log_caps=caps)
                coordinator, _hass, _ = self._make(api, ring_on_keypress=True)

                await self._run_listener_once(coordinator)

                expected = ["CallStateChanged"] if caps == ["CallStateChanged"] else BASE_EVENTS
                self.assertEqual(api.subscriptions[0], expected)
                self.assertNotIn("KeyPressed", coordinator.subscribed_log_events)

    async def test_key_pressed_follows_caps_changes(self) -> None:
        api = _FakeAPI(log_caps=BASE_EVENTS + ["KeyPressed"])
        coordinator, hass, _ = self._make(api, ring_on_keypress=True)
        await self._run_listener_once(coordinator)
        self.assertEqual(api.subscriptions[0], BASE_EVENTS + ["KeyPressed"])
        await hass.drain()

        coordinator._log_subscription_id = 1
        api.log_caps = BASE_EVENTS
        await coordinator._async_refresh_caps_from_event()
        self.assertTrue(coordinator._log_resubscribe_requested)
        self.assertEqual(coordinator._build_log_event_list(), BASE_EVENTS)

        coordinator._log_resubscribe_requested = False
        coordinator._subscribed_log_events = tuple(BASE_EVENTS)
        api.log_caps = BASE_EVENTS + ["KeyPressed"]
        await coordinator._async_refresh_caps_from_event()
        self.assertTrue(coordinator._log_resubscribe_requested)
        self.assertEqual(coordinator._build_log_event_list(), BASE_EVENTS + ["KeyPressed"])


class KeyPressedRingTests(_CoordinatorTestCase):
    def _make_ring(self, **kwargs):
        kwargs.setdefault("ring_on_keypress", True)
        kwargs.setdefault("ring_snapshot_enabled", True)
        coordinator, hass, api = self._make(**kwargs)
        hass.loop = asyncio.get_running_loop()
        self.addAsyncCleanup(coordinator.async_stop_log_listener)
        return coordinator, hass, api

    async def test_bus_keypress_ring_once_in_either_order_with_call_events(self) -> None:
        for key_first in (True, False):
            with self.subTest(key_first=key_first):
                coordinator, hass, _ = self._make_ring(ring_snapshot_enabled=False)
                call_events = [
                    _call("CallSessionStateChanged", "ringing", originator="button", info="1"),
                    _call("CallStateChanged", "ringing", direction="outgoing"),
                ]
                events = [_key("%1"), *call_events] if key_first else [*call_events, _key("%1")]
                coordinator._process_log_event(events[0])
                first_ring_time = coordinator.last_ring_time
                for event in events[1:]:
                    coordinator._process_log_event(event)
                self.assertEqual(coordinator.ring_count, 1)
                self.assertEqual(len(hass.bus.events), 1)
                name, payload = hass.bus.events[0]
                self.assertEqual(name, "2n_intercom_event")
                self.assertEqual(payload["type"], "ring")
                self.assertEqual(payload["button"], 1)
                self.assertEqual(payload["timestamp"], first_ring_time.isoformat())
                self.assertEqual(coordinator.bus_events_fired, {"ring": 1})

    async def test_call_button_press_rings_once_with_snapshot(self) -> None:
        coordinator, hass, api = self._make_ring()

        self.assertTrue(coordinator._process_log_event(_key("%1")))
        await hass.drain()

        self.assertTrue(coordinator.ring_active)
        self.assertEqual(coordinator.ring_count, 1)
        self.assertEqual(coordinator.last_ring_button, 1)
        self.assertIsNotNone(coordinator.last_ring_time.tzinfo)
        self.assertEqual(len(api.snapshot_calls), 1)
        self.assertEqual(coordinator.ring_snapshot, b"jpeg-1")

    async def test_call_button_press_without_snapshot_option(self) -> None:
        coordinator, hass, api = self._make_ring(ring_snapshot_enabled=False)

        coordinator._process_log_event(_key("%2"))
        await hass.drain()

        self.assertEqual(coordinator.ring_count, 1)
        self.assertEqual(coordinator.last_ring_button, 2)
        self.assertIs(type(coordinator.last_ring_button), int)
        self.assertEqual(api.snapshot_calls, [])

    async def test_keypad_keys_never_ring(self) -> None:
        for key in (
            *"0123456789", "*", "#", "", "%", "%a", "1%1",
            "%0", "%55", "%100", "%01", "%1\n%2",
        ):
            with self.subTest(key=key):
                coordinator, hass, api = self._make_ring()

                self.assertFalse(coordinator._process_log_event(_key(key)))
                await hass.drain()

                self.assertFalse(coordinator.ring_active)
                self.assertEqual(coordinator.ring_count, 0)
                self.assertIsNone(coordinator.last_ring_button)
                self.assertEqual(api.snapshot_calls, [])

    async def test_all_documented_call_button_keys_ring(self) -> None:
        for number in range(1, 55):
            with self.subTest(number=number):
                coordinator, hass, api = self._make_ring()
                self.assertTrue(coordinator._process_log_event(_key(f"%{number}")))
                await hass.drain()
                self.assertEqual(coordinator.ring_count, 1)
                self.assertEqual(coordinator.last_ring_button, number)
                self.assertEqual(len(api.snapshot_calls), 1)

    async def test_key_values_are_never_logged(self) -> None:
        coordinator, hass, _api = self._make_ring()
        with self.assertLogs(self.module._LOGGER, level="DEBUG") as logs:
            for key in (*"0123456789", "*", "#", "%54", "%54"):
                coordinator._process_log_event(_key(key))
        await hass.drain()

        self.assertEqual(coordinator.ring_count, 1)
        for record in logs.records:
            self.assertFalse(record.args)
            self.assertNotIn("%54", record.getMessage())
            self.assertNotIn("#", record.getMessage())
            self.assertNotIn("*", record.getMessage())
            self.assertFalse(any(character.isdigit() for character in record.getMessage()))

    async def test_pin_entry_does_not_ring(self) -> None:
        coordinator, hass, api = self._make_ring()

        for key in ("1", "2", "3", "4", "#"):
            coordinator._process_log_event(_key(key))
        await hass.drain()

        self.assertEqual(coordinator.ring_count, 0)
        self.assertEqual(api.snapshot_calls, [])

    async def test_option_off_ignores_call_button(self) -> None:
        coordinator, hass, api = self._make_ring(ring_on_keypress=False)

        self.assertFalse(coordinator._process_log_event(_key("%1")))
        await hass.drain()

        self.assertEqual(coordinator.ring_count, 0)
        self.assertEqual(api.snapshot_calls, [])

    async def test_invalid_params_are_ignored(self) -> None:
        coordinator, _hass, _api = self._make_ring()

        self.assertFalse(coordinator._process_log_event(
            {"event": "KeyPressed", "params": "garbage"}
        ))
        self.assertEqual(coordinator.ring_count, 0)

    async def test_press_that_starts_a_call_rings_once(self) -> None:
        sequences = {
            "key first": [
                _key("%1"),
                _call("CallStateChanged", "ringing", direction="outgoing"),
                _call("CallSessionStateChanged", "ringing", originator="button"),
            ],
            "call first": [
                _call("CallStateChanged", "ringing", direction="outgoing"),
                _call("CallSessionStateChanged", "ringing", originator="button"),
                _key("%1"),
            ],
            "key between calls": [
                _call("CallStateChanged", "ringing", direction="outgoing"),
                _key("%1"),
                _call("CallSessionStateChanged", "ringing", originator="button"),
            ],
            "key before connecting": [
                _key("%1"),
                _call("CallSessionStateChanged", "initialized", originator="button"),
                _call("CallStateChanged", "connecting", direction="outgoing"),
                _call("CallSessionStateChanged", "connecting", originator="button"),
                _call("CallStateChanged", "ringing", direction="outgoing"),
                _call("CallSessionStateChanged", "ringing", originator="button"),
            ],
        }
        for name, sequence in sequences.items():
            with self.subTest(order=name):
                coordinator, hass, api = self._make_ring()

                for event in sequence:
                    coordinator._process_log_event(event)
                await hass.drain()

                self.assertEqual(coordinator.ring_count, 1)
                self.assertEqual(len(api.snapshot_calls), 1)
                self.assertEqual(coordinator.active_session_id, "135")

                coordinator._process_log_event(
                    _call("CallSessionStateChanged", "terminated")
                )
                self.assertFalse(coordinator.ring_active)
                self.assertEqual(coordinator.ring_count, 1)

    async def test_button_is_fixed_when_keypress_and_call_events_arrive_in_either_order(self) -> None:
        for key_first in (True, False):
            with self.subTest(key_first=key_first):
                coordinator, hass, api = self._make_ring()
                key_event = _key("%2")
                call_events = [
                    _call("CallSessionStateChanged", "initialized", originator="button", info="2"),
                    _call("CallStateChanged", "connecting", direction="outgoing"),
                    _call("CallSessionStateChanged", "connecting", originator="button", info="2"),
                    _call("CallStateChanged", "ringing", direction="outgoing"),
                    _call("CallSessionStateChanged", "ringing", originator="button", info="2"),
                ]
                events = (
                    [key_event, *call_events] if key_first else [*call_events, key_event]
                )
                for event in events:
                    coordinator._process_log_event(event)
                    if coordinator.ring_count:
                        self.assertEqual(coordinator.ring_count, 1)
                        self.assertEqual(coordinator.last_ring_button, 2)
                await hass.drain()
                self.assertEqual(len(api.snapshot_calls), 1)

    async def test_duplicate_event_does_not_replace_counted_button(self) -> None:
        for key_first in (True, False):
            with self.subTest(key_first=key_first):
                coordinator, hass, _api = self._make_ring()
                session_event = _call(
                    "CallSessionStateChanged", "ringing", originator="button", info="12",
                )
                key_event = _key("%2")
                events = (
                    (key_event, session_event) if key_first else (session_event, key_event)
                )
                for event in events:
                    coordinator._process_log_event(event)
                    self.assertEqual(coordinator.ring_count, 1)
                    self.assertEqual(coordinator.last_ring_button, 2 if key_first else 12)
                await hass.drain()

    async def test_keypress_overwrites_previous_call_button(self) -> None:
        coordinator, hass, _api = self._make_ring()
        coordinator._process_log_event(_call(
            "CallSessionStateChanged", "ringing", originator="button", info="12",
        ))
        coordinator._process_log_event(_call("CallSessionStateChanged", "terminated"))
        coordinator._process_log_event(_key("%2"))
        await hass.drain()
        self.assertEqual(coordinator.ring_count, 2)
        self.assertEqual(coordinator.last_ring_button, 2)

    async def test_press_ring_does_not_report_an_earlier_calls_peer(self) -> None:
        coordinator, hass, _api = self._make_ring()
        coordinator._process_log_event(_call(
            "CallStateChanged", "ringing", direction="outgoing",
            peer="sip:200@example.com",
        ))
        coordinator._process_log_event(_call("CallStateChanged", "terminated"))
        self.assertEqual(coordinator.called_peer, "200")

        coordinator._process_log_event(_key("%1"))
        await hass.drain()

        self.assertEqual(coordinator.ring_count, 2)
        self.assertIsNone(coordinator.called_peer)

    async def test_called_id_filter_does_not_apply_to_presses(self) -> None:
        coordinator, hass, _api = self._make_ring(called_id="sip:200@example.com")

        coordinator._process_log_event(_key("%1"))
        await hass.drain()

        self.assertEqual(coordinator.ring_count, 1)

    async def test_filtered_call_after_press_does_not_ring_again(self) -> None:
        coordinator, hass, api = self._make_ring(called_id="200")
        for event in (
            _key("%1"),
            _call("CallStateChanged", "ringing", direction="outgoing", peer="100"),
            _call("CallSessionStateChanged", "ringing", originator="button"),
        ):
            coordinator._process_log_event(event)
        await hass.drain()

        self.assertEqual(coordinator.ring_count, 1)
        self.assertEqual(len(api.snapshot_calls), 1)

    async def test_terminal_call_state_still_clears_press_ring(self) -> None:
        coordinator, hass, api = self._make_ring()
        coordinator._process_log_event(_key("%1"))
        coordinator._process_log_event(_call("CallStateChanged", "terminated"))
        self.assertFalse(coordinator.ring_active)

        coordinator._process_log_event(_key("%1"))
        await hass.drain()
        self.assertEqual(coordinator.ring_count, 2)
        self.assertEqual(len(api.snapshot_calls), 2)

    async def test_key_only_ring_clears_and_next_press_rings_again(self) -> None:
        coordinator, hass, api = self._make_ring()
        hass.loop = asyncio.get_running_loop()

        with patch.object(self.module, "KEYPRESS_RING_HOLD", 0.01):
            coordinator._process_log_event(_key("%1"))
            self.assertTrue(coordinator.ring_active)
            await asyncio.sleep(0.05)

            self.assertFalse(coordinator.ring_active)
            coordinator.async_update_listeners.assert_called()

            coordinator._process_log_event(_key("%1"))
            await hass.drain()

        self.assertEqual(coordinator.ring_count, 2)
        self.assertEqual(len(api.snapshot_calls), 2)

    async def test_clear_leaves_a_ring_that_a_call_took_over(self) -> None:
        coordinator, hass, _api = self._make_ring()
        hass.loop = asyncio.get_running_loop()

        with patch.object(self.module, "KEYPRESS_RING_HOLD", 0.01):
            coordinator._process_log_event(_key("%1"))
            coordinator._process_log_event(
                _call("CallStateChanged", "ringing", direction="outgoing")
            )
            await asyncio.sleep(0.05)

        self.assertTrue(coordinator._ring_detected)
        self.assertEqual(coordinator.ring_count, 1)
        await hass.drain()

    async def test_stop_log_listener_cancels_pending_clear(self) -> None:
        coordinator, hass, _api = self._make_ring()
        hass.loop = asyncio.get_running_loop()

        coordinator._process_log_event(_key("%1"))
        self.assertIsNotNone(coordinator._keypress_clear_handle)
        await coordinator.async_stop_log_listener()

        self.assertIsNone(coordinator._keypress_clear_handle)
        await hass.drain()
