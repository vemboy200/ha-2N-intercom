"""Coordinator tests for log subscriptions, access-control events and ring snapshots."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from _stubs import EventBus
from test_coordinator import load_coordinator_module

BASE_EVENTS = [
    "CallStateChanged",
    "CallSessionStateChanged",
    "SwitchStateChanged",
    "InputChanged",
    "OutputChanged",
    "RegistrationStateChanged",
    "ConfigurationChanged",
    "CapabilitiesChanged",
    "DeviceState",
]

IP_UNI_LOG_CAPS = [
    "KeyPressed",
    "KeyReleased",
    "OutputChanged",
    "SwitchStateChanged",
    "SwitchOperationChanged",
    "SwitchesBlocked",
    "ApiAccessRequested",
    "DtmfEntered",
    "CallSessionStateChanged",
    "CallStateChanged",
    "RegistrationStateChanged",
    "DeviceState",
    "LoginBlocked",
    "HardwareChanged",
    "DirectoryChanged",
    "DirectorySaved",
    "CapabilitiesChanged",
    "ConfigurationChanged",
]


class _Hass:
    """Collects scheduled tasks so tests can await them deterministically."""

    def __init__(self) -> None:
        self.tasks: list[asyncio.Future] = []
        self.bus = EventBus()

    def async_create_task(self, coro):
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task

    async def drain(self) -> None:
        while self.tasks:
            pending, self.tasks = self.tasks, []
            await asyncio.gather(*pending, return_exceptions=True)


class _FakeAPI:
    def __init__(self, log_caps=None, users=None, snapshot=b"jpeg-1") -> None:
        self.log_caps = log_caps
        self.users = users if users is not None else []
        self.snapshot = snapshot
        self.directory_calls = 0
        self.snapshot_calls: list[dict] = []
        self.subscriptions: list[list[str]] = []
        self.pull_calls = 0

    async def async_get_log_caps(self):
        if isinstance(self.log_caps, Exception):
            raise self.log_caps
        return self.log_caps

    async def async_get_directory_users(self):
        self.directory_calls += 1
        if isinstance(self.users, Exception):
            raise self.users
        return list(self.users)

    async def async_get_snapshot(self, width=None, height=None, source=None):
        self.snapshot_calls.append({"width": width, "height": height, "source": source})
        if isinstance(self.snapshot, Exception):
            raise self.snapshot
        return self.snapshot

    async def async_subscribe_log(self, events):
        self.subscriptions.append(list(events))
        return len(self.subscriptions)

    async def async_pull_log(self, sub_id, timeout=1):
        self.pull_calls += 1
        raise RuntimeError("stop")

    async def async_unsubscribe_log(self, sub_id):
        return True

    async def async_get_switch_caps(self):
        return {}

    async def async_get_io_caps(self):
        return {}


class _CoordinatorTestCase(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_coordinator_module()

    def _make(self, api=None, **kwargs):
        hass = _Hass()
        api = api or _FakeAPI()
        coordinator = self.module.TwoNIntercomCoordinator(hass, api, **kwargs)
        coordinator.async_request_refresh = AsyncMock()
        coordinator.async_update_listeners = Mock()
        coordinator.data = self.module.TwoNIntercomData(
            call_status={},
            last_ring_time=None,
            caller_info=None,
            active_session_id=None,
            available=True,
            phone_status={},
            switch_caps={},
            switch_status={},
            io_caps={},
            io_status={},
        )
        return coordinator, hass, api

    async def _run_listener_once(self, coordinator) -> None:
        async def pull_and_stop(sub_id, timeout=1):
            coordinator.api.pull_calls += 1
            coordinator._log_listener_stopped = True
            return []

        coordinator.api.async_pull_log = pull_and_stop
        await asyncio.wait_for(coordinator._async_log_listener_loop(), timeout=5)


class LogCapsSubscriptionTests(_CoordinatorTestCase):
    async def test_subscribes_only_supported_access_events(self) -> None:
        api = _FakeAPI(
            log_caps=[
                "CallStateChanged",
                "CallSessionStateChanged",
                "CardEntered",
                "CodeEntered",
                "UserAuthenticated",
                "DirectoryChanged",
                "SomeFutureEvent",
            ]
        )
        coordinator, _hass, _ = self._make(api)

        await self._run_listener_once(coordinator)

        subscribed = api.subscriptions[0]
        self.assertEqual(
            subscribed,
            [
                "CallStateChanged",
                "CallSessionStateChanged",
                "CardEntered",
                "CodeEntered",
                "UserAuthenticated",
                "DirectoryChanged",
            ],
        )
        self.assertNotIn("FingerEntered", subscribed)
        self.assertNotIn("SomeFutureEvent", subscribed)
        self.assertEqual(coordinator.subscribed_log_events, tuple(subscribed))
        self.assertIn("SomeFutureEvent", coordinator.log_caps)

    async def test_all_access_events_when_device_supports_them(self) -> None:
        const = __import__("sys").modules["custom_components.2n_intercom.const"]
        api = _FakeAPI(
            log_caps=BASE_EVENTS + ["MotionDetected"] + list(const.ACCESS_LOG_EVENTS)
        )
        coordinator, _hass, _ = self._make(api)
        coordinator._system_caps = {"motionDetection": "active,licensed"}

        await self._run_listener_once(coordinator)

        self.assertEqual(
            api.subscriptions[0],
            BASE_EVENTS + ["MotionDetected"] + list(const.ACCESS_LOG_EVENTS),
        )

    async def test_no_access_events_without_log_caps(self) -> None:
        for caps in (None, [], RuntimeError("caps failed"), "garbage"):
            for motion_available in (False, True):
                with self.subTest(caps=caps, motion_available=motion_available):
                    api = _FakeAPI(log_caps=caps)
                    coordinator, _hass, _ = self._make(api)
                    if motion_available:
                        coordinator._system_caps = {"motionDetection": "active,licensed"}

                    with self.assertNoLogs(self.module._LOGGER, level="WARNING"):
                        await self._run_listener_once(coordinator)

                    expected = BASE_EVENTS + (["MotionDetected"] if motion_available else [])
                    self.assertEqual(coordinator._build_log_event_list(), expected)
                    self.assertEqual(api.subscriptions[0], expected)

    async def test_ip_uni_caps_filter_unsupported_base_events(self) -> None:
        api = _FakeAPI(log_caps=IP_UNI_LOG_CAPS)
        coordinator, _hass, _ = self._make(api)

        with self.assertNoLogs(self.module._LOGGER, level="WARNING"):
            await self._run_listener_once(coordinator)

        expected = [event for event in BASE_EVENTS if event != "InputChanged"]
        expected += ["ApiAccessRequested", "DirectoryChanged"]
        self.assertEqual(coordinator._build_log_event_list(), expected)
        self.assertEqual(api.subscriptions[0], expected)
        self.assertLessEqual(set(api.subscriptions[0]), set(IP_UNI_LOG_CAPS))
        self.assertNotIn("InputChanged", api.subscriptions[0])
        self.assertIn("CallStateChanged", api.subscriptions[0])
        self.assertIn("CallSessionStateChanged", api.subscriptions[0])

    async def test_motion_requires_system_and_log_capabilities_when_known(self) -> None:
        for motion_available in (False, True):
            for motion_listed in (False, True):
                with self.subTest(available=motion_available, listed=motion_listed):
                    caps = BASE_EVENTS + (["MotionDetected"] if motion_listed else [])
                    api = _FakeAPI(log_caps=caps)
                    coordinator, _hass, _ = self._make(api)
                    coordinator._system_caps = {
                        "motionDetection": "active,licensed" if motion_available else "licensed"
                    }

                    await self._run_listener_once(coordinator)

                    expected = BASE_EVENTS + (
                        ["MotionDetected"] if motion_available and motion_listed else []
                    )
                    self.assertEqual(coordinator._build_log_event_list(), expected)
                    self.assertEqual(api.subscriptions[0], expected)

    async def test_missing_call_events_warn_once_and_still_subscribe(self) -> None:
        for missing in (
            {"CallStateChanged"},
            {"CallSessionStateChanged"},
            {"CallStateChanged", "CallSessionStateChanged"},
        ):
            with self.subTest(missing=missing):
                caps = [event for event in BASE_EVENTS if event not in missing]
                api = _FakeAPI(log_caps=caps)
                coordinator, _hass, _ = self._make(api)

                with self.assertLogs(self.module._LOGGER, level="WARNING") as logs:
                    await self._run_listener_once(coordinator)
                    self.assertEqual(coordinator._build_log_event_list(), caps)
                    coordinator._log_caps = frozenset(BASE_EVENTS)
                    coordinator._build_log_event_list()
                    coordinator._log_caps = frozenset(caps)
                    coordinator._build_log_event_list()

                self.assertEqual(len(logs.records), 1)
                self.assertEqual(logs.records[0].levelname, "WARNING")
                self.assertEqual(
                    logs.records[0].getMessage(),
                    "Ring detection is not available on this device: "
                    "log capabilities lack CallStateChanged or CallSessionStateChanged",
                )
                self.assertEqual(api.subscriptions, [caps])
                self.assertEqual(coordinator.subscribed_log_events, tuple(caps))

    async def test_no_access_events_when_api_lacks_log_caps(self) -> None:
        api = _FakeAPI()
        api.async_get_log_caps = None
        coordinator, _hass, _ = self._make(api)

        await self._run_listener_once(coordinator)

        self.assertEqual(api.subscriptions[0], BASE_EVENTS)

    async def test_failed_caps_refresh_keeps_previous_caps(self) -> None:
        api = _FakeAPI(log_caps=["CardEntered"])
        coordinator, _hass, _ = self._make(api)
        await coordinator._async_refresh_log_caps()
        api.log_caps = RuntimeError("temporary")
        await coordinator._async_refresh_log_caps()
        self.assertEqual(coordinator.log_caps, frozenset({"CardEntered"}))

    async def test_caps_change_requests_resubscribe(self) -> None:
        api = _FakeAPI(log_caps=[])
        coordinator, hass, _ = self._make(api)
        coordinator._log_subscription_id = 7
        coordinator._subscribed_log_events = tuple(BASE_EVENTS)

        await coordinator._async_refresh_caps_from_event()
        self.assertFalse(coordinator._log_resubscribe_requested)

        api.log_caps = ["CardEntered"]
        self.assertFalse(
            coordinator._process_log_event({"event": "CapabilitiesChanged", "params": {}})
        )
        await hass.drain()
        self.assertTrue(coordinator._log_resubscribe_requested)

    async def test_resubscribe_request_ends_pull_loop_without_pulling(self) -> None:
        api = _FakeAPI()
        coordinator, _hass, _ = self._make(api)
        coordinator._log_resubscribe_requested = True

        await coordinator._async_run_log_subscription(3)

        self.assertEqual(api.pull_calls, 0)
        self.assertFalse(coordinator._log_resubscribe_requested)

    async def test_caps_events_resubscribe_with_changed_base_event_filter(self) -> None:
        without_input = [event for event in BASE_EVENTS if event != "InputChanged"]
        for event_name in ("CapabilitiesChanged", "ConfigurationChanged"):
            for initial, updated in ((BASE_EVENTS, without_input), (without_input, BASE_EVENTS)):
                with self.subTest(event=event_name, initial=initial):
                    api = _FakeAPI(log_caps=initial)
                    coordinator, hass, _ = self._make(api)

                    async def pull(sub_id, timeout=1):
                        if sub_id == 1:
                            api.log_caps = updated
                            return [{"event": event_name, "params": {}}]
                        coordinator._log_listener_stopped = True
                        return []

                    api.async_pull_log = pull
                    await asyncio.wait_for(coordinator._async_log_listener_loop(), timeout=5)
                    await hass.drain()

                    self.assertEqual(api.subscriptions, [initial, updated])
                    self.assertEqual(coordinator.subscribed_log_events, tuple(updated))
                    self.assertFalse(coordinator._log_resubscribe_requested)

    async def test_malformed_access_event_does_not_break_pull_loop(self) -> None:
        coordinator, _hass, api = self._make()
        batches = [
            [
                {"event": "CardEntered", "params": "garbage"},
                {"event": "CardEntered"},
                {"event": "UnknownAccessThing", "params": {}},
                {"event": "CardEntered", "params": {"valid": True, "ap": 0}},
            ]
        ]

        async def pull(sub_id, timeout=1):
            if batches:
                return batches.pop()
            coordinator._log_listener_stopped = True
            return []

        api.async_pull_log = pull
        await coordinator._async_run_log_subscription(1)

        event = coordinator.data.last_access_event
        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "CardEntered")
        self.assertTrue(event.granted)
        coordinator.async_update_listeners.assert_called()


class AccessEventParsingTests(_CoordinatorTestCase):
    def _process(self, coordinator, name, params, **extra):
        event = {"id": 1, "utcTime": 1548078453, "upTime": 10, "event": name, "params": params}
        event.update(extra)
        return coordinator._process_log_event(event)

    def _assert_no_secrets(self, event, *secrets) -> None:
        dumped = repr(event.as_dict())
        for secret in secrets:
            self.assertNotIn(secret, dumped)

    async def test_card_entered(self) -> None:
        coordinator, _hass, _ = self._make()
        self.assertTrue(
            self._process(
                coordinator,
                "CardEntered",
                {
                    "reader": "internal",
                    "uid": "39BC8AE2",
                    "valid": True,
                    "ap": 0,
                    "session": 5,
                    "direction": "IN",
                    "uuid": "ABC-1",
                },
            )
        )
        event = coordinator.last_access_event
        self.assertEqual(event.event_type, "CardEntered")
        self.assertEqual(event.credential_type, "card")
        self.assertTrue(event.granted)
        self.assertEqual(event.access_point, 0)
        self.assertEqual(event.direction, "in")
        self.assertEqual(event.session, "5")
        self.assertEqual(event.user_uuid, "abc-1")
        self.assertEqual(event.timestamp, datetime(2019, 1, 21, 13, 47, 33, tzinfo=timezone.utc))
        self._assert_no_secrets(event, "39BC8AE2")

    async def test_code_entered_invalid_with_string_flag(self) -> None:
        coordinator, _hass, _ = self._make()
        self.assertTrue(
            self._process(
                coordinator,
                "CodeEntered",
                {"ap": "1", "session": "8", "code": "1234", "valid": "false", "reason": "invalidCode"},
            )
        )
        event = coordinator.last_access_event
        self.assertEqual(event.credential_type, "code")
        self.assertFalse(event.granted)
        self.assertEqual(event.access_point, 1)
        self.assertEqual(event.reason, "invalidCode")
        self._assert_no_secrets(event, "1234")

    async def test_mobile_key_entered(self) -> None:
        coordinator, _hass, _ = self._make()
        self.assertTrue(
            self._process(
                coordinator,
                "MobKeyEntered",
                {"action": "tap", "authid": "SECRET-AUTH", "valid": True, "ap": 0},
            )
        )
        event = coordinator.last_access_event
        self.assertEqual(event.credential_type, "mobile_key")
        self.assertTrue(event.granted)
        self._assert_no_secrets(event, "SECRET-AUTH")

    async def test_finger_entered(self) -> None:
        coordinator, _hass, _ = self._make()
        self.assertTrue(self._process(coordinator, "FingerEntered", {"valid": False}))
        event = coordinator.last_access_event
        self.assertEqual(event.credential_type, "fingerprint")
        self.assertFalse(event.granted)
        self.assertIsNone(event.access_point)

    async def test_user_authenticated_inherits_credential_from_session(self) -> None:
        coordinator, _hass, _ = self._make()
        self._process(coordinator, "CardEntered", {"valid": True, "ap": 0, "session": 3, "uid": "x"})
        self.assertTrue(
            self._process(
                coordinator,
                "UserAuthenticated",
                {"ap": 0, "session": 3, "name": "Alice", "uuid": "U-1"},
            )
        )
        event = coordinator.last_access_event
        self.assertEqual(event.event_type, "UserAuthenticated")
        self.assertTrue(event.granted)
        self.assertEqual(event.credential_type, "card")
        self.assertEqual(event.user_name, "Alice")
        self.assertEqual(event.user_uuid, "u-1")

    async def test_session_credential_is_per_access_point(self) -> None:
        coordinator, _hass, _ = self._make()
        self._process(coordinator, "CardEntered", {"valid": True, "ap": 0, "session": 3})
        self._process(coordinator, "UserAuthenticated", {"ap": 1, "session": 3, "name": "A"})
        self.assertIsNone(coordinator.last_access_event.credential_type)

    async def test_user_rejected(self) -> None:
        coordinator, _hass, _ = self._make()
        self._process(coordinator, "CodeEntered", {"valid": True, "ap": 0, "session": 4, "code": "9"})
        self.assertTrue(
            self._process(
                coordinator,
                "UserRejected",
                {"ap": 0, "session": 4, "name": "Bob", "reason": "invalidTime"},
            )
        )
        event = coordinator.last_access_event
        self.assertFalse(event.granted)
        self.assertEqual(event.reason, "invalidTime")
        self.assertEqual(event.credential_type, "code")

    async def test_access_taken(self) -> None:
        coordinator, _hass, _ = self._make()
        self.assertTrue(self._process(coordinator, "AccessTaken", {"ap": 0, "session": 2}))
        event = coordinator.last_access_event
        self.assertEqual(event.event_type, "AccessTaken")
        self.assertIsNone(event.granted)

    async def test_access_blocked_in_and_out(self) -> None:
        coordinator, _hass, _ = self._make()
        self.assertTrue(self._process(coordinator, "AccessBlocked", {"ap": 0, "state": "in"}))
        self.assertFalse(coordinator.last_access_event.granted)
        self.assertEqual(coordinator.last_access_event.state, "in")
        self.assertTrue(self._process(coordinator, "AccessBlocked", {"ap": 0, "state": "out"}))
        self.assertIsNone(coordinator.last_access_event.granted)
        self.assertEqual(coordinator.last_access_event.state, "out")

    async def test_access_limited_maps_type(self) -> None:
        coordinator, _hass, _ = self._make()
        for raw, expected in (("finger", "fingerprint"), ("card", "card"), ("code", "code"), ("x", None)):
            self.assertTrue(
                self._process(coordinator, "AccessLimited", {"ap": 1, "type": raw, "state": "in"})
            )
            self.assertEqual(coordinator.last_access_event.credential_type, expected)
            self.assertFalse(coordinator.last_access_event.granted)

    async def test_api_access_requested(self) -> None:
        coordinator, _hass, _ = self._make()
        self.assertTrue(
            self._process(coordinator, "ApiAccessRequested", {"ap": 0, "valid": True, "uuid": "U-9"})
        )
        event = coordinator.last_access_event
        self.assertEqual(event.credential_type, "api")
        self.assertTrue(event.granted)
        self.assertEqual(event.user_uuid, "u-9")

    async def test_malformed_events_are_ignored_without_raising(self) -> None:
        coordinator, _hass, _ = self._make()
        self._process(coordinator, "FingerEntered", {"valid": True})
        previous = coordinator.last_access_event
        malformed = [
            ("CardEntered", ["not", "a", "dict"]),
            ("CardEntered", {"uid": "x"}),
            ("CodeEntered", {"valid": "maybe"}),
            ("AccessBlocked", {"ap": 0}),
            ("AccessLimited", {"ap": 0, "type": "card", "state": "sideways"}),
        ]
        for name, params in malformed:
            self.assertFalse(self._process(coordinator, name, params), name)
        self.assertIs(coordinator.last_access_event, previous)

    async def test_odd_field_types_are_tolerated(self) -> None:
        coordinator, _hass, _ = self._make()
        self.assertTrue(
            self._process(
                coordinator,
                "UserAuthenticated",
                {"ap": True, "session": {"x": 1}, "uuid": ["u"], "name": None},
            )
        )
        event = coordinator.last_access_event
        self.assertIsNone(event.access_point)
        self.assertIsNone(event.session)
        self.assertIsNone(event.user_uuid)
        self.assertIsNone(event.user_name)

    async def test_timestamp_falls_back_to_tz_aware_now(self) -> None:
        coordinator, _hass, _ = self._make()
        before = datetime.now(timezone.utc)
        coordinator._process_log_event({"event": "FingerEntered", "params": {"valid": True}})
        timestamp = coordinator.last_access_event.timestamp
        self.assertIsNotNone(timestamp.tzinfo)
        self.assertGreaterEqual(timestamp, before)
        coordinator._process_log_event(
            {"event": "FingerEntered", "utcTime": "bogus", "params": {"valid": True}}
        )
        self.assertIsNotNone(coordinator.last_access_event.timestamp.tzinfo)

    async def test_access_event_reaches_coordinator_data(self) -> None:
        coordinator, _hass, _ = self._make()
        self._process(coordinator, "CardEntered", {"valid": True})
        coordinator._publish_event_state()
        self.assertIs(coordinator.data.last_access_event, coordinator.last_access_event)

    async def test_as_dict_is_serialisable(self) -> None:
        coordinator, _hass, _ = self._make()
        self._process(coordinator, "UserAuthenticated", {"ap": 0, "session": 1, "name": "A", "uuid": "U"})
        data = coordinator.last_access_event.as_dict()
        self.assertEqual(data["timestamp"], "2019-01-21T13:47:33+00:00")
        self.assertEqual(data["user_name"], "A")
        self.assertEqual(
            set(data),
            {
                "event_type",
                "timestamp",
                "credential_type",
                "access_point",
                "direction",
                "granted",
                "reason",
                "state",
                "user_uuid",
                "user_name",
                "session",
            },
        )


class AccessBusEventTests(_CoordinatorTestCase):
    TYPES = {
        "CardEntered": "card_entered",
        "CodeEntered": "code_entered",
        "MobKeyEntered": "mobile_key_entered",
        "FingerEntered": "fingerprint_entered",
        "UserAuthenticated": "user_authenticated",
        "UserRejected": "user_rejected",
        "AccessTaken": "access_taken",
        "AccessBlocked": "access_blocked",
        "AccessLimited": "access_limited",
        "ApiAccessRequested": "api_access_requested",
    }
    PAYLOAD_KEYS = {
        "device_id", "config_entry_id", "type", "timestamp", "credential_type",
        "access_point", "direction", "granted", "reason", "state", "user_uuid",
        "user_name", "session",
    }

    def _event(self, event_name, **params):
        return {"event": event_name, "utcTime": 1548078453, "params": params}

    async def test_all_access_types_fire_once_with_exact_parsed_payload(self) -> None:
        const = sys.modules["custom_components.2n_intercom.const"]
        self.assertEqual(set(const.ACCESS_LOG_EVENTS), set(const.ACCESS_BUS_EVENT_TYPES))
        self.assertEqual(const.ACCESS_BUS_EVENT_TYPES, self.TYPES)
        for name, bus_type in self.TYPES.items():
            with self.subTest(event=name):
                coordinator, hass, _ = self._make(
                    config_entry=types.SimpleNamespace(entry_id="entry-1")
                )
                params = {
                    "valid": "false", "ap": "1", "session": 8, "direction": "IN",
                    "uuid": "USER-1", "state": "in", "type": "finger",
                    "reason": "invalidCode",
                }
                if name in {"UserAuthenticated", "UserRejected"}:
                    params["name"] = "Test Visitor"
                coordinator._directory_names = {"user-1": "Cached Visitor"}

                self.assertTrue(coordinator._process_log_event(self._event(name, **params)))

                self.assertEqual(len(hass.bus.events), 1)
                event_name, payload = hass.bus.events[0]
                self.assertEqual(event_name, "2n_intercom_event")
                self.assertEqual(set(payload), self.PAYLOAD_KEYS)
                expected = coordinator.last_access_event.as_dict()
                del expected["event_type"]
                expected.update(device_id=None, config_entry_id="entry-1", type=bus_type)
                self.assertEqual(payload, expected)
                timestamp = datetime.fromisoformat(payload["timestamp"])
                self.assertEqual(
                    timestamp, datetime(2019, 1, 21, 13, 47, 33, tzinfo=timezone.utc)
                )
                self.assertEqual(timestamp.utcoffset().total_seconds(), 0)
                self.assertEqual(coordinator.bus_events_fired, {bus_type: 1})

    async def test_unknown_access_fields_are_present_as_null(self) -> None:
        coordinator, hass, _ = self._make()
        coordinator._process_log_event(self._event("AccessTaken"))
        payload = hass.bus.events[0][1]
        self.assertEqual(set(payload), self.PAYLOAD_KEYS)
        for key in self.PAYLOAD_KEYS - {"type", "timestamp"}:
            self.assertIsNone(payload[key], key)

    async def test_one_pull_batch_fires_every_event_in_device_order(self) -> None:
        coordinator, hass, api = self._make()
        batch = [
            self._event("CardEntered", valid=True, ap=0, session=1),
            self._event("UserAuthenticated", ap=0, session=1, name="Test Visitor"),
            self._event("AccessTaken", ap=0, session=1),
        ]

        async def pull_and_stop(subscription_id, timeout):
            coordinator._log_listener_stopped = True
            return batch

        api.async_pull_log = AsyncMock(side_effect=pull_and_stop)
        await coordinator._async_run_log_subscription(1)

        self.assertEqual(
            [data["type"] for _, data in hass.bus.events],
            ["card_entered", "user_authenticated", "access_taken"],
        )
        self.assertEqual(
            [data["credential_type"] for _, data in hass.bus.events], ["card"] * 3
        )
        self.assertEqual(coordinator.data.last_access_event.event_type, "AccessTaken")
        coordinator.async_update_listeners.assert_called_once()

    async def test_names_use_event_then_directory_without_backfill_refiring(self) -> None:
        api = _FakeAPI(users=[{"uuid": "USER-1", "name": "Directory Visitor"}])
        coordinator, hass, _ = self._make(api)
        coordinator._process_log_event(
            self._event("CardEntered", valid=True, uuid="USER-1")
        )
        self.assertIsNone(hass.bus.events[0][1]["user_name"])
        await hass.drain()
        self.assertEqual(coordinator.last_access_event.user_name, "Directory Visitor")
        self.assertEqual(len(hass.bus.events), 1)
        self.assertIsNone(hass.bus.events[0][1]["user_name"])

        for name in ("CodeEntered", "MobKeyEntered", "FingerEntered",
                     "AccessTaken", "AccessBlocked", "AccessLimited", "ApiAccessRequested"):
            coordinator._process_log_event(
                self._event(name, valid=True, state="in", uuid="USER-1")
            )
            self.assertEqual(hass.bus.events[-1][1]["user_name"], "Directory Visitor")
        for name in ("UserAuthenticated", "UserRejected"):
            coordinator._process_log_event(
                self._event(name, uuid="USER-1", name="Event Visitor")
            )
            self.assertEqual(hass.bus.events[-1][1]["user_name"], "Event Visitor")

        coordinator._process_log_event(
            self._event("CardEntered", valid=True, uuid="UNKNOWN-USER")
        )
        await hass.drain()
        self.assertIsNone(hass.bus.events[-1][1]["user_name"])
        self.assertEqual(len(hass.bus.events), 11)
        self.assertEqual(sum(coordinator.bus_events_fired.values()), 11)

    async def test_credentials_and_raw_parameters_never_reach_bus_or_logs(self) -> None:
        coordinator, hass, _ = self._make()
        secrets = {
            "code": "SECRET-PIN", "uid": "SECRET-CARD",
            "authid": "SECRET-MOBILE", "reader": "SECRET-READER",
            "extra": "SECRET-RAW",
        }
        coordinator._directory_names = {"private-user": "Private Visitor"}
        with self.assertLogs(self.module._LOGGER, level="DEBUG") as logs:
            for name in ("CodeEntered", "CardEntered", "MobKeyEntered"):
                coordinator._process_log_event(
                    self._event(name, valid=False, uuid="PRIVATE-USER", **secrets)
                )
            coordinator._process_log_event(self._event("CardEntered", **secrets))
        self.assertEqual(len(hass.bus.events), 3)
        for _, payload in hass.bus.events:
            for key, value in secrets.items():
                self.assertNotIn(key, payload)
                self.assertNotIn(value, repr(payload))
        for value in (*secrets.values(), "PRIVATE-USER", "private-user", "Private Visitor"):
            self.assertNotIn(value, "\n".join(logs.output))

    async def test_rejected_events_do_not_fire_or_break_next_event_in_batch(self) -> None:
        coordinator, hass, api = self._make()
        rejected = [
            {"event": "CardEntered", "params": ["malformed"]},
            self._event("CodeEntered"),
            self._event("AccessBlocked", state="unknown"),
            self._event("AccessLimited", state="unknown"),
        ]

        async def pull_and_stop(subscription_id, timeout):
            coordinator._log_listener_stopped = True
            return rejected + [self._event("FingerEntered", valid=True)]

        api.async_pull_log = AsyncMock(side_effect=pull_and_stop)
        await coordinator._async_run_log_subscription(1)
        self.assertEqual(
            [payload["type"] for _, payload in hass.bus.events], ["fingerprint_entered"]
        )
        self.assertEqual(coordinator.bus_events_fired, {"fingerprint_entered": 1})
        self.assertEqual(coordinator.data.last_access_event.event_type, "FingerEntered")

    async def test_non_user_facing_events_never_fire(self) -> None:
        coordinator, hass, _ = self._make(ring_on_keypress=True)
        for name, params in (
            ("MotionDetected", {"state": "in"}),
            ("SwitchStateChanged", {"switch": 1, "active": True}),
            ("InputChanged", {"port": "input1", "state": True}),
            ("OutputChanged", {"port": "output1", "state": True}),
            ("RegistrationStateChanged", {"account": 1, "state": "registered"}),
            ("ConfigurationChanged", {}),
            ("CapabilitiesChanged", {}),
            ("DeviceState", {"state": "startup"}),
            ("DirectoryChanged", {}),
            ("KeyPressed", {"key": "#"}),
        ):
            coordinator._process_log_event(self._event(name, **params))
            self.assertEqual(hass.bus.events, [], name)
        await hass.drain()
        self.assertEqual(coordinator.bus_events_fired, {})

    async def test_device_id_retried_until_found_then_cached(self) -> None:
        coordinator, hass, _ = self._make(
            config_entry=types.SimpleNamespace(entry_id="entry-1")
        )
        registry = sys.modules["homeassistant.helpers.device_registry"].async_get(hass)
        registry.devices[("2n_intercom", "entry-other")] = types.SimpleNamespace(id="other")
        with patch.object(registry, "async_get_device", wraps=registry.async_get_device) as lookup:
            coordinator._process_log_event(self._event("CardEntered", valid=True))
            self.assertIsNone(hass.bus.events[-1][1]["device_id"])
            registry.devices[("2n_intercom", "entry-1")] = types.SimpleNamespace(id="device-1")
            coordinator._process_log_event(self._event("CodeEntered", valid=True))
            self.assertEqual(hass.bus.events[-1][1]["device_id"], "device-1")
            registry.devices.clear()
            coordinator._process_log_event(self._event("FingerEntered", valid=True))
            self.assertEqual(hass.bus.events[-1][1]["device_id"], "device-1")
            self.assertEqual(lookup.call_count, 2)
            lookup.assert_called_with(identifiers={("2n_intercom", "entry-1")})
        for _, payload in hass.bus.events:
            self.assertEqual(payload["config_entry_id"], "entry-1")

    async def test_bus_types_follow_subscription_and_counts_are_defensive_copies(self) -> None:
        coordinator, hass, _ = self._make()
        self.assertEqual(coordinator.bus_event_types, [])
        self.assertEqual(coordinator.bus_events_fired, {})
        for ring_source in ("CallStateChanged", "CallSessionStateChanged", "KeyPressed"):
            coordinator._subscribed_log_events = (
                ring_source, "CardEntered", "CodeEntered", "MotionDetected", "DirectoryChanged"
            )
            self.assertEqual(coordinator.bus_event_types, ["card_entered", "code_entered", "ring"])
        coordinator._subscribed_log_events = tuple(self.TYPES)
        self.assertEqual(coordinator.bus_event_types, sorted(self.TYPES.values()))
        coordinator._process_log_event(self._event("CardEntered", valid=True))
        coordinator._process_log_event(self._event("CardEntered", valid=False))
        counts = coordinator.bus_events_fired
        self.assertEqual(counts, {"card_entered": 2})
        counts["card_entered"] = 99
        coordinator.bus_event_types.clear()
        self.assertEqual(coordinator.bus_events_fired, {"card_entered": 2})
        self.assertEqual(coordinator.bus_event_types, sorted(self.TYPES.values()))
        self.assertEqual(len(hass.bus.events), 2)


class DirectoryNameResolutionTests(_CoordinatorTestCase):
    async def test_event_name_populates_cache_for_later_events(self) -> None:
        coordinator, hass, api = self._make()
        coordinator._process_log_event(
            {"event": "UserAuthenticated", "params": {"ap": 0, "session": 1, "name": "Alice", "uuid": "AA-1"}}
        )
        coordinator._process_log_event(
            {"event": "CardEntered", "params": {"valid": True, "uuid": "aa-1"}}
        )
        await hass.drain()
        self.assertEqual(coordinator.last_access_event.user_name, "Alice")
        self.assertEqual(api.directory_calls, 0)

    async def test_unknown_uuid_resolved_via_directory_and_backfilled(self) -> None:
        api = _FakeAPI(users=[{"uuid": "BB-2", "name": "Bob"}, {"uuid": "CC-3", "name": "Carol"}])
        coordinator, hass, _ = self._make(api)

        coordinator._process_log_event({"event": "CardEntered", "params": {"valid": True, "uuid": "bb-2"}})
        self.assertIsNone(coordinator.last_access_event.user_name)
        await hass.drain()

        self.assertEqual(api.directory_calls, 1)
        self.assertEqual(coordinator.last_access_event.user_name, "Bob")
        self.assertEqual(coordinator.data.last_access_event.user_name, "Bob")
        coordinator.async_update_listeners.assert_called()

        # Cached now: no further directory query for a known uuid.
        coordinator._process_log_event({"event": "CardEntered", "params": {"valid": True, "uuid": "CC-3"}})
        await hass.drain()
        self.assertEqual(coordinator.last_access_event.user_name, "Carol")
        self.assertEqual(api.directory_calls, 1)

    async def test_uuid_missing_from_directory_stays_unnamed_and_is_throttled(self) -> None:
        api = _FakeAPI(users=[{"uuid": "BB-2", "name": "Bob"}])
        coordinator, hass, _ = self._make(api)

        coordinator._process_log_event({"event": "CardEntered", "params": {"valid": True, "uuid": "zz-9"}})
        await hass.drain()
        coordinator._process_log_event({"event": "CardEntered", "params": {"valid": True, "uuid": "zz-9"}})
        await hass.drain()

        self.assertIsNone(coordinator.last_access_event.user_name)
        self.assertEqual(api.directory_calls, 1)

    async def test_directory_failure_is_tolerated(self) -> None:
        api = _FakeAPI(users=RuntimeError("dir failed"))
        coordinator, hass, _ = self._make(api)
        coordinator._process_log_event({"event": "CardEntered", "params": {"valid": True, "uuid": "x"}})
        await hass.drain()
        self.assertIsNone(coordinator.last_access_event.user_name)

    async def test_directory_changed_invalidates_cache_and_throttle(self) -> None:
        api = _FakeAPI(users=[{"uuid": "BB-2", "name": "Bob"}])
        coordinator, hass, _ = self._make(api)
        coordinator._process_log_event({"event": "CardEntered", "params": {"valid": True, "uuid": "bb-2"}})
        await hass.drain()
        self.assertEqual(api.directory_calls, 1)

        api.users = [{"uuid": "BB-2", "name": "Robert"}]
        self.assertFalse(
            coordinator._process_log_event({"event": "DirectoryChanged", "params": {"series": "1"}})
        )
        coordinator._process_log_event({"event": "CardEntered", "params": {"valid": True, "uuid": "bb-2"}})
        await hass.drain()

        self.assertEqual(api.directory_calls, 2)
        self.assertEqual(coordinator.last_access_event.user_name, "Robert")

    async def test_backfill_skips_when_a_newer_event_arrived(self) -> None:
        api = _FakeAPI(users=[{"uuid": "BB-2", "name": "Bob"}])
        coordinator, hass, _ = self._make(api)
        coordinator._process_log_event({"event": "CardEntered", "params": {"valid": True, "uuid": "bb-2"}})
        coordinator._process_log_event({"event": "FingerEntered", "params": {"valid": True}})
        await hass.drain()
        self.assertEqual(coordinator.last_access_event.event_type, "FingerEntered")
        self.assertIsNone(coordinator.last_access_event.user_name)


class RingSnapshotTests(_CoordinatorTestCase):
    def _ring(self, coordinator, event="CallStateChanged", direction="outgoing", state="ringing"):
        params = {"state": state, "session": 1}
        if event == "CallSessionStateChanged":
            params["originator"] = "button"
        else:
            params["direction"] = direction
        return coordinator._process_log_event(
            {"event": event, "params": params}
        )

    async def test_ring_captures_snapshot_once_per_ring(self) -> None:
        api_module = __import__("sys").modules["custom_components.2n_intercom.api"]
        coordinator, hass, api = self._make(ring_snapshot_enabled=True)
        coordinator._camera_transport_info = api_module.CameraTransportInfo(source="external")

        before = datetime.now(timezone.utc)
        self._ring(coordinator)
        self._ring(coordinator, event="CallSessionStateChanged")
        await hass.drain()

        self.assertEqual(len(api.snapshot_calls), 1)
        self.assertEqual(api.snapshot_calls[0]["source"], "external")
        self.assertEqual(coordinator.ring_snapshot, b"jpeg-1")
        self.assertEqual(coordinator.data.ring_snapshot, b"jpeg-1")
        self.assertIsNotNone(coordinator.ring_snapshot_time.tzinfo)
        self.assertGreaterEqual(coordinator.ring_snapshot_time, before)
        self.assertEqual(coordinator.data.ring_snapshot_time, coordinator.ring_snapshot_time)
        coordinator.async_update_listeners.assert_called()

        # Ring ends, a new ring captures a fresh frame.
        self._ring(coordinator, state="terminated")
        api.snapshot = b"jpeg-2"
        self._ring(coordinator)
        await hass.drain()
        self.assertEqual(len(api.snapshot_calls), 2)
        self.assertEqual(coordinator.ring_snapshot, b"jpeg-2")

    async def test_no_capture_when_disabled_or_incoming(self) -> None:
        coordinator, hass, api = self._make()
        self._ring(coordinator)
        await hass.drain()
        self.assertEqual(api.snapshot_calls, [])

        coordinator, hass, api = self._make(ring_snapshot_enabled=True)
        self._ring(coordinator, direction="incoming")
        await hass.drain()
        self.assertEqual(api.snapshot_calls, [])

    async def test_snapshot_failure_keeps_previous_image_and_warns(self) -> None:
        coordinator, hass, api = self._make(ring_snapshot_enabled=True)
        self._ring(coordinator)
        await hass.drain()
        first_time = coordinator.ring_snapshot_time

        for failure in (RuntimeError("camera down"), None, b""):
            self._ring(coordinator, state="idle")
            api.snapshot = failure
            with self.assertLogs(self.module._LOGGER, level="WARNING") as logs:
                self._ring(coordinator)
                await hass.drain()
            self.assertIn("keeping previous image", "\n".join(logs.output))
            self.assertEqual(coordinator.ring_snapshot, b"jpeg-1")
            self.assertEqual(coordinator.ring_snapshot_time, first_time)

    async def test_update_data_carries_event_state(self) -> None:
        coordinator, hass, api = self._make(ring_snapshot_enabled=True)

        async def call_status():
            return {"state": "idle", "sessions": []}

        async def system_info():
            return {}

        api.async_get_call_status = call_status
        api.async_get_system_info = system_info
        self._ring(coordinator)
        await hass.drain()
        coordinator._process_log_event({"event": "FingerEntered", "params": {"valid": True}})

        data = await coordinator._async_update_data()

        self.assertEqual(data.ring_snapshot, b"jpeg-1")
        self.assertIsNotNone(data.ring_snapshot_time)
        self.assertEqual(data.last_access_event.event_type, "FingerEntered")


if __name__ == "__main__":
    unittest.main()
