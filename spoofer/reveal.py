"""Un-hide the Developer Mode toggle in the connected iPhone's Settings app."""
from __future__ import annotations

import asyncio
import sys

from .device import DeviceError, LocationSession


async def run() -> int:
    print("\n  Asking the phone to show its Developer Mode toggle…\n")
    try:
        result = await LocationSession.reveal_developer_mode()
    except DeviceError as exc:
        print(f"  \033[31m✗ {exc.message}\033[0m")
        if exc.hint:
            print(f"    \033[90m{exc.hint}\033[0m")
        print()
        return 1

    if result.get("already_enabled"):
        print("  \033[32m✓ Developer Mode is already on.\033[0m Nothing to do — go ahead and connect.\n")
        return 0

    print("  \033[32m✓ Done.\033[0m Now, on the phone:\n")
    print("     1. Settings → Privacy & Security → \033[1mDeveloper Mode\033[0m (it should be there now)")
    print("     2. Turn it on. The phone restarts.")
    print("     3. After it boots, unlock it and tap Turn On at the prompt.")
    print("     4. Come back and hit Connect.\n")
    print("  \033[90mIf the toggle still isn't there: check that Lockdown Mode is off")
    print("  (Settings → Privacy & Security → Lockdown Mode), and that the phone is")
    print("  really trusted. macOS also ships 'devmodectl single' as a second way in.")
    print("  Supervision only blocks this indirectly, by refusing to pair at all —")
    print("  and that fails loudly, well before this step.\033[0m\n")
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
