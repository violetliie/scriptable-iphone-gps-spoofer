"""Owns the connection to the iPhone and the loop that feeds it coordinates.

The whole trick is Apple's own developer tooling. Xcode's "Simulate Location"
talks to an on-device DTX service called
``com.apple.instruments.server.services.LocationSimulation``. From iOS 17 that
service only answers over a RemoteXPC tunnel, so the sequence is:

    usbmux -> lockdown -> mount the personalized DDI -> RSD tunnel -> DTX -> LocationSimulation

CoreLocation then reports the injected fix system-wide until the DTX channel
closes, which is why this module keeps one open for the life of the session.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from pymobiledevice3.exceptions import AlreadyMountedError, PasswordRequiredError
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.mobile_image_mounter import auto_mount
from pymobiledevice3.usbmux import list_devices

from . import favorites, geo

log = logging.getLogger("spoofer.device")

TICK_SECONDS = 0.5
# Re-assert a stationary fix on this cadence. The DTX channel makes the fix
# sticky on its own, but re-sending keeps CoreLocation's timestamps fresh so
# consumers don't treat the fix as stale.
KEEPALIVE_SECONDS = 2.0
# Generous: a first-time mount downloads ~15 MB and does a round trip to Apple's signing
# server. Anything past this is a wedged service, not slow progress.
MOUNT_TIMEOUT = 180.0


class DeviceError(Exception):
    """A user-fixable problem, phrased for display in the UI."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


@dataclass
class Target:
    """What the mover should be doing right now."""

    mode: str = "idle"  # idle | fixed | route | joystick
    lat: Optional[float] = None
    lon: Optional[float] = None
    heading: float = 0.0
    speed_kmh: float = 0.0
    jitter_m: float = 0.0
    route: Optional[geo.Route] = None
    route_points: list[tuple[float, float]] = field(default_factory=list)
    paused: bool = False


class MockSimulation:
    """Stands in for the device so the UI and movement engine can be exercised offline."""

    def __init__(self) -> None:
        self.fixes: list[tuple[float, float]] = []

    async def set(self, latitude: float, longitude: float) -> None:
        self.fixes.append((latitude, longitude))

    async def clear(self) -> None:
        self.fixes.clear()


class LocationSession:
    """Connects to one device and streams simulated coordinates to it."""

    def __init__(self, mock: bool = False) -> None:
        self.mock = mock
        self._tunnel: Optional[UserspaceRsdTunnel] = None
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self._sim: Optional[LocationSimulation] = None
        self._mover: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

        self.status = "disconnected"  # disconnected | connecting | connected | error
        self.error: Optional[str] = None
        self.hint: str = ""
        self.progress: str = ""
        self.device: dict[str, Any] = {}
        self.target = Target()
        # `current` is the position on the intended path; `sent` is what actually went to
        # the device. They differ by the GPS-noise offset. Movement maths always uses
        # `current`, otherwise the noise would compound into a random walk.
        self.current: Optional[tuple[float, float]] = None
        self.sent: Optional[tuple[float, float]] = None
        self.last_push: float = 0.0
        self.pushes: int = 0
        self._rng = random.Random()
        self._listeners: list[Callable[[], None]] = []

    # ---------------------------------------------------------------- state

    def subscribe(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb) if cb in self._listeners else None

    def _notify(self) -> None:
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                log.debug("listener failed", exc_info=True)

    def snapshot(self) -> dict[str, Any]:
        lat, lon = (self.current or (None, None))
        sent_lat, sent_lon = (self.sent or (None, None))
        route_pts = self.target.route_points
        return {
            "status": self.status,
            "error": self.error,
            "hint": self.hint,
            "progress": self.progress,
            "device": self.device,
            "mode": self.target.mode,
            "paused": self.target.paused,
            "lat": lat,
            "lon": lon,
            "sent_lat": sent_lat,
            "sent_lon": sent_lon,
            "heading": round(self.target.heading, 1),
            "speed_kmh": self.target.speed_kmh,
            "jitter_m": self.target.jitter_m,
            "route": [list(p) for p in route_pts],
            "route_done": bool(self.target.route and self.target.route.finished),
            "pushes": self.pushes,
            "last_push": self.last_push,
        }

    # ------------------------------------------------------------ discovery

    @staticmethod
    async def list_attached() -> list[dict[str, Any]]:
        """Devices usbmuxd can see right now, with the details needed for preflight."""
        out = []
        for mux in await list_devices():
            entry = {
                "udid": mux.serial,
                "connection": mux.connection_type,
                "name": None,
                "ios": None,
                "model": None,
                "developer_mode": None,
                "paired": None,
            }
            try:
                lockdown = await create_using_usbmux(serial=mux.serial, autopair=False)
                entry["paired"] = True
                entry["name"] = lockdown.display_name or lockdown.short_info.get("DeviceName")
                entry["ios"] = lockdown.product_version
                entry["model"] = lockdown.product_type
                with contextlib.suppress(Exception):
                    entry["developer_mode"] = await lockdown.get_developer_mode_status()
                await _close_quietly(lockdown)
            except Exception as exc:  # not paired / locked / trust dialog pending
                entry["paired"] = False
                entry["detail"] = str(exc)
            out.append(entry)
        return out

    @staticmethod
    async def reveal_developer_mode(udid: Optional[str] = None) -> dict[str, Any]:
        """Ask AMFI to un-hide the Developer Mode toggle in the device's Settings app.

        iOS ships with that menu item hidden. It only appears once a development tool on a
        trusted computer asks for it, which writes AMFI's `AMFIShowOverridePath` flag file.
        Xcode does this implicitly; here it is an explicit step.

        Flipping the toggle afterwards has to happen on the phone by hand whenever a passcode
        is set, which is why this only reveals and never tries to enable.
        """
        from pymobiledevice3.services.amfi import AmfiService

        devices = await list_devices()
        usb = [d for d in devices if d.is_usb] or devices
        if udid:
            usb = [d for d in usb if d.serial == udid] or usb
        if not usb:
            raise DeviceError(
                "No iPhone found.",
                "Plug the phone in with a cable, unlock it, and tap Trust if prompted.",
            )

        try:
            lockdown = await create_using_usbmux(serial=usb[0].serial, autopair=True)
        except Exception as exc:
            raise DeviceError(
                f"Could not talk to the device: {exc}",
                "Unlock the phone and accept the 'Trust This Computer' prompt, then retry.",
            ) from exc

        try:
            already = await lockdown.get_developer_mode_status()
        except Exception:
            already = None

        if already:
            await _close_quietly(lockdown)
            return {"revealed": True, "already_enabled": True}

        try:
            await AmfiService(lockdown).reveal_developer_mode_option_in_ui()
        except Exception as exc:
            raise DeviceError(
                f"The device refused to reveal Developer Mode: {exc}",
                "Check that the phone is unlocked and that Lockdown Mode is off "
                "(Settings → Privacy & Security → Lockdown Mode).",
            ) from exc
        finally:
            await _close_quietly(lockdown)

        return {"revealed": True, "already_enabled": False}

    # ------------------------------------------------------------- lifecycle

    async def connect(self, udid: Optional[str] = None) -> None:
        async with self._lock:
            if self.status == "connected":
                return
            self.status = "connecting"
            self.error = None
            self.hint = ""
            self._notify()
            try:
                await self._connect_inner(udid)
            except DeviceError as exc:
                await self._teardown()
                self.status = "error"
                self.error = exc.message
                self.hint = exc.hint
                self._notify()
                raise
            except Exception as exc:
                await self._teardown()
                self.status = "error"
                self.error = f"{type(exc).__name__}: {exc}"
                self.hint = "See the terminal running the server for the full traceback."
                log.exception("connect failed")
                self._notify()
                raise DeviceError(self.error, self.hint) from exc

    async def _connect_inner(self, udid: Optional[str]) -> None:
        if self.mock:
            self._set_progress("Mock mode — no device is being touched.")
            await asyncio.sleep(0.2)
            self.device = {"udid": "MOCK", "name": "Mock iPhone", "ios": "26.0", "model": "iPhone17,1"}
            self._sim = MockSimulation()  # type: ignore[assignment]
            self.status = "connected"
            self.progress = ""
            self._mover = asyncio.create_task(self._move_loop())
            self._notify()
            return

        self._set_progress("Looking for a device over USB…")
        devices = await list_devices()
        usb = [d for d in devices if d.is_usb] or devices
        if not usb:
            raise DeviceError(
                "No iPhone found.",
                "Plug the phone in with a cable, unlock it, and tap Trust if prompted.",
            )
        if udid:
            match = [d for d in usb if d.serial == udid]
            if not match:
                raise DeviceError(f"Device {udid} is not attached.", "Pick a different device.")
            usb = match
        serial = usb[0].serial

        self._set_progress("Pairing over lockdown…")
        try:
            lockdown = await create_using_usbmux(serial=serial, autopair=True)
        except Exception as exc:
            raise DeviceError(
                f"Could not talk to lockdownd: {exc}",
                "Unlock the phone and accept the 'Trust This Computer' prompt, then retry.",
            ) from exc

        self.device = {
            "udid": serial,
            "name": lockdown.display_name or lockdown.short_info.get("DeviceName"),
            "ios": lockdown.product_version,
            "model": lockdown.product_type,
        }
        self._notify()

        try:
            dev_mode = await lockdown.get_developer_mode_status()
        except Exception:
            dev_mode = None
        if dev_mode is False:
            await _close_quietly(lockdown)
            raise DeviceError(
                "Developer Mode is off on the device.",
                "Settings → Privacy & Security → Developer Mode → on, then restart the phone.",
            )

        self._set_progress("Mounting the Developer Disk Image…")
        try:
            # The device-side mounter can wedge (typically after an interrupted mount) and
            # then never answer at all, so don't wait on it forever.
            await asyncio.wait_for(auto_mount(lockdown), timeout=MOUNT_TIMEOUT)
        except AlreadyMountedError:
            pass
        except asyncio.TimeoutError as exc:
            await _close_quietly(lockdown)
            raise DeviceError(
                "The phone stopped responding while mounting the developer image.",
                "Unplug the cable and plug it back in, then hit Connect again. If it still "
                "hangs, restart the phone — that always clears it.",
            ) from exc
        except PasswordRequiredError as exc:
            await _close_quietly(lockdown)
            raise DeviceError(
                "The phone is locked.",
                "Unlock it and keep the screen on, then hit Connect again. Mounting the "
                "developer image needs an unlocked device.",
            ) from exc
        except Exception as exc:
            await _close_quietly(lockdown)
            raise DeviceError(
                f"Could not mount the Developer Disk Image: {exc}",
                "The DDI is downloaded once for this iOS build, so this needs internet and a "
                "device that isn't mid-update.",
            ) from exc

        await _close_quietly(lockdown)

        self._set_progress("Opening the RemoteXPC tunnel…")
        tunnel = UserspaceRsdTunnel(serial=serial, autopair=True)
        try:
            rsd = await tunnel.aopen()
        except Exception as exc:
            raise DeviceError(
                f"Could not open the tunnel: {exc}",
                "iOS 17.4 and newer tunnel over USB with no root. On iOS 17.0–17.3 you need "
                "'sudo pymobiledevice3 remote tunneld' running separately.",
            ) from exc
        self._tunnel = tunnel

        self._set_progress("Attaching to the location service…")
        stack = contextlib.AsyncExitStack()
        try:
            dvt = await stack.enter_async_context(DvtProvider(rsd))
            sim = await stack.enter_async_context(LocationSimulation(dvt))
        except Exception as exc:
            await stack.aclose()
            raise DeviceError(
                f"Could not open the LocationSimulation channel: {exc}",
                "This is the same channel Xcode uses; it needs the DDI mounted and the phone unlocked.",
            ) from exc

        self._stack = stack
        self._sim = sim
        self.status = "connected"
        self.progress = ""
        self._mover = asyncio.create_task(self._move_loop())
        self._notify()
        log.info("connected to %s (iOS %s)", self.device.get("name"), self.device.get("ios"))

    async def disconnect(self) -> None:
        async with self._lock:
            await self._teardown()
            self.status = "disconnected"
            self.progress = ""
            self._notify()

    async def _teardown(self) -> None:
        if self._mover:
            self._mover.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._mover
            self._mover = None
        if self._sim:
            with contextlib.suppress(Exception):
                await self._sim.clear()
        if self._stack:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
            self._stack = None
        self._sim = None
        if self._tunnel:
            with contextlib.suppress(Exception):
                await self._tunnel.aclose()
            self._tunnel = None
        self.target = Target()
        self.current = None
        self.sent = None

    def _set_progress(self, text: str) -> None:
        self.progress = text
        log.info(text)
        self._notify()

    # ---------------------------------------------------------------- moves

    def _require_connected(self) -> LocationSimulation:
        if self.status != "connected" or self._sim is None:
            raise DeviceError("Not connected to a device.", "Hit Connect first.")
        return self._sim

    async def teleport(self, lat: float, lon: float) -> None:
        self._require_connected()
        self.target.mode = "fixed"
        self.target.lat, self.target.lon = geo.clamp_lat(lat), geo.wrap_lon(lon)
        self.target.route = None
        self.target.route_points = []
        self.target.speed_kmh = 0.0
        self.target.paused = False
        await self._push(self.target.lat, self.target.lon)
        favorites.remember_last(self.target.lat, self.target.lon)

    async def start_route(
        self,
        points: list[tuple[float, float]],
        speed_kmh: float,
        loop: bool = False,
        ping_pong: bool = False,
    ) -> None:
        self._require_connected()
        route = geo.Route(points, loop=loop, ping_pong=ping_pong)
        self.target.mode = "route"
        self.target.route = route
        self.target.route_points = list(points)
        self.target.speed_kmh = max(0.1, speed_kmh)
        self.target.paused = False
        lat, lon, head = route.position()
        self.target.lat, self.target.lon, self.target.heading = lat, lon, head
        await self._push(lat, lon)

    async def joystick(self, heading: float, speed_kmh: float) -> None:
        self._require_connected()
        if self.current is None:
            raise DeviceError("Set a starting point first.", "Click the map to drop your position.")
        self.target.mode = "joystick"
        self.target.heading = heading % 360.0
        self.target.speed_kmh = max(0.0, speed_kmh)
        self.target.route = None
        self.target.route_points = []
        self.target.paused = False
        self._notify()

    async def stop_moving(self) -> None:
        """Freeze in place but keep the simulated fix applied."""
        self._require_connected()
        if self.current:
            self.target.lat, self.target.lon = self.current
        self.target.mode = "fixed" if self.current else "idle"
        self.target.speed_kmh = 0.0
        self.target.route = None
        self.target.route_points = []
        self._notify()

    def set_paused(self, paused: bool) -> None:
        self.target.paused = paused
        self._notify()

    def set_jitter(self, metres: float) -> None:
        self.target.jitter_m = max(0.0, min(50.0, metres))
        self._notify()

    def set_speed(self, speed_kmh: float) -> None:
        self.target.speed_kmh = max(0.0, speed_kmh)
        self._notify()

    async def clear(self) -> None:
        """Hand control back to the real GPS."""
        sim = self._require_connected()
        await sim.clear()
        self.target = Target()
        self.current = None
        self.sent = None
        self._notify()

    # ----------------------------------------------------------- the engine

    async def _push(self, lat: float, lon: float) -> None:
        sim = self._require_connected()
        out_lat, out_lon = geo.jitter(lat, lon, self.target.jitter_m, self._rng)
        try:
            await sim.set(out_lat, out_lon)
        except Exception as exc:
            log.warning("push failed, dropping the session: %s", exc)
            self.status = "error"
            self.error = f"Lost the device: {exc}"
            self.hint = "Reconnect the cable and hit Connect again."
            self._notify()
            raise DeviceError(self.error, self.hint) from exc
        self.current = (lat, lon)
        self.sent = (out_lat, out_lon)
        self.last_push = time.time()
        self.pushes += 1
        self._notify()

    async def _move_loop(self) -> None:
        """Advance the simulated position and keep pushing it to the device."""
        last = time.monotonic()
        while True:
            await asyncio.sleep(TICK_SECONDS)
            now = time.monotonic()
            dt, last = now - last, now
            t = self.target
            if self.status != "connected" or t.paused:
                continue
            try:
                if t.mode == "route" and t.route is not None:
                    if t.route.finished:
                        continue
                    t.route.advance(t.speed_kmh * 1000.0 / 3600.0 * dt)
                    lat, lon, head = t.route.position()
                    t.lat, t.lon, t.heading = lat, lon, head
                    await self._push(lat, lon)
                elif t.mode == "joystick" and self.current is not None:
                    if t.speed_kmh <= 0:
                        await self._keepalive()
                        continue
                    lat, lon = geo.destination(
                        *self.current, t.heading, t.speed_kmh * 1000.0 / 3600.0 * dt
                    )
                    t.lat, t.lon = lat, lon
                    await self._push(lat, lon)
                elif t.mode == "fixed":
                    await self._keepalive()
            except DeviceError:
                return  # _push already flipped us into the error state
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("mover tick failed")

    async def _keepalive(self) -> None:
        t = self.target
        if t.lat is None or t.lon is None:
            return
        if time.time() - self.last_push < KEEPALIVE_SECONDS:
            return
        await self._push(t.lat, t.lon)


async def _close_quietly(client: Any) -> None:
    for name in ("aclose", "close"):
        fn = getattr(client, name, None)
        if fn is None:
            continue
        try:
            result = fn()
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass
        return
