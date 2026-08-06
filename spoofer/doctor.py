"""Preflight check: walks the same chain the app does and reports where it breaks."""
from __future__ import annotations

import asyncio
import contextlib
import sys

from packaging.version import Version

OK, WARN, BAD, DASH = "\033[32m✓\033[0m", "\033[33m!\033[0m", "\033[31m✗\033[0m", "\033[90m·\033[0m"


def line(mark: str, text: str, detail: str = "") -> None:
    print(f"  {mark} {text}")
    if detail:
        for part in detail.split("\n"):
            print(f"      \033[90m{part}\033[0m")


async def run() -> int:
    from pymobiledevice3.lockdown import create_using_usbmux
    from pymobiledevice3.services.mobile_image_mounter import PersonalizedImageMounter
    from pymobiledevice3.usbmux import list_devices

    from .device import _close_quietly

    print("\n  iPhone Location Simulator — preflight\n")
    failures = 0

    try:
        devices = await list_devices()
    except Exception as exc:
        line(BAD, "usbmuxd is not reachable", str(exc))
        return 1
    line(OK, f"usbmuxd reachable ({len(devices)} device(s) visible)")

    usb = [d for d in devices if d.is_usb]
    if not usb:
        line(BAD, "No device attached over USB", "Plug the phone in with a cable and unlock it.")
        return 1

    for mux in usb:
        print(f"\n  \033[1m{mux.serial}\033[0m ({mux.connection_type})")
        try:
            lockdown = await create_using_usbmux(serial=mux.serial, autopair=False)
        except Exception as exc:
            line(BAD, "Not paired / lockdown refused", f"{exc}\nUnlock the phone and tap 'Trust This Computer'.")
            failures += 1
            continue

        name = lockdown.display_name or lockdown.short_info.get("DeviceName") or "iPhone"
        version = lockdown.product_version
        line(OK, f"Paired — {name}, iOS {version}")

        if Version(version) < Version("17.0"):
            line(WARN, "iOS < 17: uses the legacy lockdown DDI path, not the RSD tunnel",
                 "This app targets iOS 17+. Older devices need the com.apple.dt.simulatelocation service.")

        try:
            dev_mode = await lockdown.get_developer_mode_status()
            if dev_mode:
                line(OK, "Developer Mode is on")
            else:
                line(BAD, "Developer Mode is OFF",
                     "Settings → Privacy & Security → Developer Mode → on, then restart the phone.\n"
                     "If that entry is not in Settings at all, iOS is still hiding it: run\n"
                     "./'Show Developer Mode.command' (or ./.venv/bin/python -m spoofer.reveal) first.")
                failures += 1
        except Exception as exc:
            line(WARN, "Could not read Developer Mode status", str(exc))

        try:
            mounted = await PersonalizedImageMounter(lockdown).is_image_mounted("Personalized")
            if mounted:
                line(OK, "Developer Disk Image is already mounted")
            else:
                line(DASH, "DDI not mounted yet — the app mounts it on connect",
                     "First mount downloads the image and needs internet access.")
        except Exception as exc:
            line(WARN, "Could not query the image mounter", str(exc))

        await _close_quietly(lockdown)

        print("\n  Opening the RemoteXPC tunnel (this is the iOS 17+ gate)…")
        try:
            from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

            tunnel = UserspaceRsdTunnel(serial=mux.serial, autopair=True)
            rsd = await tunnel.aopen()
            line(OK, f"Tunnel up — RSD at {rsd.service.address[0] if hasattr(rsd, 'service') else 'in-process'}")
            try:
                from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
                from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation

                async with DvtProvider(rsd) as dvt, LocationSimulation(dvt):
                    line(OK, "LocationSimulation channel opened — everything the app needs is working")
            except Exception as exc:
                line(BAD, "LocationSimulation channel refused", str(exc))
                failures += 1
            finally:
                with contextlib.suppress(Exception):
                    await tunnel.aclose()
        except Exception as exc:
            line(BAD, "Tunnel failed", f"{exc}\nOn iOS 17.0–17.3 run 'sudo pymobiledevice3 remote tunneld' separately.")
            failures += 1

    print()
    if failures:
        print(f"  \033[31m{failures} blocking issue(s).\033[0m Fix the ✗ lines above, then rerun.\n")
    else:
        print("  \033[32mReady.\033[0m Start the app with ./run.sh\n")
    return 1 if failures else 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
