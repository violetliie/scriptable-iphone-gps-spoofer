# iPhone Location Simulator

Change your iPhone's reported GPS location from a map in your browser. Plug the phone into a
Mac, click somewhere, and every app on the phone believes that is where you are. No jailbreak,
nothing installed on the phone, nothing modified.

## Why I built this

I have strict asian parents, iykyk. I wanted to still go out in uni without my parents finding
out on their Find My. But I didn't trust those free GPS spoofers (with & without jailbreak),
didn't want to give out my bank information to some hacker.

The plan was simple: build a thing that puts me at the library while I am demonstrably not at
the library, ship it, go outside, lol.

Then I found out the fake location only holds while the phone is plugged into my laptop. Unplug
it and the real GPS is back instantly. So I can be at the library, as long as I am at home.

I built a machine whose only function is to prove I am at home (or where ever they trust).

Anyway, it turns out to be a genuinely useful developer tool, so here it is.

## What it actually is

This is a reimplementation of what commercial tools like iMyFone AnyTo, iToolab AnyGo and
Dr.Fone Virtual Location sell for around $40 a year. There is no proprietary technology in any
of them. They are all a map UI wrapped around one Apple developer feature: the location
simulator built into Xcode.

Useful for testing geofencing, regional content gating, delivery and rideshare flows, location
based notifications, and anything else where you would otherwise have to physically walk
somewhere to test your app.

## Disclaimer

**This is published for research and educational purposes.** It documents a supported Apple
developer interface and reimplements it in the open so people can see how the commercial tools
in this category actually work.

Use it on hardware you own. Do not use it to defeat anti-cheat systems, fraud controls,
licensing restrictions, or anyone's terms of service.

Worth knowing before you get ideas: **this is trivially detectable.** iOS exposes
`CLLocation.sourceInformation.isSimulatedBySoftware`, and any app that cares (banks, some
games, some dating apps) can read it in one line. This is a developer feature, not a stealth
feature. It was never designed to fool anybody and it does not.

The authors accept no responsibility for what you do with it.

## How it works

When you pick "Simulate Location" in Xcode's debug bar, Xcode opens a channel to a service on
the device called `com.apple.instruments.server.services.LocationSimulation` and calls one
selector, `simulateLocationWithLatitude:longitude:`. CoreLocation then reports that fix to
every app on the phone, not just the one being debugged, and it holds until the channel closes.

That is the entire feature. Everything else is ceremony to get a socket to it.

Historically the path was short. From iOS 17, Apple moved the developer services behind
RemoteXPC and replaced the static Developer Disk Image with a personalized one that Apple's
signing server issues per device and per build. The chain today:

```
usbmux (Apple's own daemon, already running on every Mac)
  |
  +-- lockdownd                    pair and trust
        |
        +-- mobile_image_mounter   mount the personalized DDI
              |
              +-- CoreDeviceProxy  open an RSD tunnel (the iOS 17+ gate)
                    |
                    +-- RemoteXPC -> com.apple.instruments.dtservicehub
                          |
                          +-- DTX channel: LocationSimulation
                                +-- simulateLocationWithLatitude:longitude:
                                +-- stopLocationSimulation
```

The heavy lifting is done by [pymobiledevice3](https://github.com/doronz88/pymobiledevice3), a
clean room Python implementation of Apple's device protocols. On iOS 17.4 and newer it builds
the tunnel entirely in userspace with a pure Python TCP stack, so this runs as a normal user
with **no sudo**, and there is no privileged helper daemon.

## Stack

Deliberately boring. No build step, no bundler, no framework, no database, no Docker, no
`node_modules`. Clone it and run it.

**Backend**

| Piece | Role |
| --- | --- |
| Python 3.10+ | Async throughout. Every device call is `asyncio`, so one process holds the tunnel, the DTX channel and the HTTP server on a single event loop. |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | Does all of the actual work: usbmux, lockdown, DDI mounting, the RemoteXPC tunnel, and the DTX protocol. GPL-3.0-or-later, which is why this project is too. |
| FastAPI + uvicorn | JSON API and the server sent event stream. Both arrive with pymobiledevice3, but are declared explicitly in `requirements.txt` so an upstream change cannot break the app. |
| No database | Saved places are one JSON file in `~/.iphone-location-simulator/`. |

**Frontend**

| Piece | Role |
| --- | --- |
| Vanilla JavaScript | One HTML file, no framework, no build. View source and the whole client is in front of you. |
| [Leaflet](https://leafletjs.com) 1.9.4 | Map rendering. Vendored into `static/vendor/` rather than pulled from a CDN, so nothing executable is fetched from a third party at runtime. BSD 2-Clause. |
| Server sent events | `/api/events` pushes session state, so the marker tracks the phone live without polling. |
| CARTO dark basemap | Tiles over OpenStreetMap data. Nominatim handles place name lookup, on Enter only. |

**Apple interfaces driven**, all through pymobiledevice3:

`usbmux` for transport, `lockdownd` for pairing, `com.apple.amfi.lockdown` to un-hide the
Developer Mode toggle, `mobile_image_mounter` for the personalized DDI, `CoreDeviceProxy` for
the RemoteXPC tunnel, and `com.apple.instruments.dtservicehub` for the DTX channel that carries
`LocationSimulation`.

**Everything else**

pytest for the geodesy, route cursor, GPX parsing and jitter distribution, none of which need a
device. A `.app` bundle and a few `.command` files so it launches from Spotlight or Finder
instead of a terminal. A `bootstrap.sh` that builds the virtualenv on first run from any entry
point, and repairs it if an install is interrupted.

## Requirements

* macOS with the Xcode command line tools. Full Xcode is not needed, and `usbmuxd` already
  ships with the OS.
* Python 3.10 or newer. It runs on 3.9, but Apple's bundled 3.9.6 is end of life and
  pymobiledevice3's own CLI cannot import on it. `brew install python@3.12` if you are on
  stock macOS Python.
* An iPhone on iOS 17.4 or newer for the no sudo path. iOS 17.0 through 17.3 works but needs
  `sudo pymobiledevice3 remote tunneld` running alongside. iOS 16 and older use a different
  service that this project does not implement.
* A USB cable. Not optional, see the backstory.

Tested on an iPhone 17 Pro running iOS 26.4.2, against macOS 26.5.

## Setup

### 1. Clone and start it

```bash
git clone https://github.com/YOUR-USERNAME/iphone-location-simulator.git
cd iphone-location-simulator
./run.sh
```

First run builds a virtualenv and installs pymobiledevice3, which takes about a minute. It then
serves the map at http://127.0.0.1:8765.

### 2. Reveal Developer Mode on the phone

iOS ships with the Developer Mode toggle **hidden**. It only appears once a development tool on
a trusted Mac asks for it. Xcode does this silently the first time you run an app on a device,
which is why most people never learn the step exists.

Plug the phone in, unlock it, tap **Trust This Computer**, then run:

```bash
./doctor.sh
```

If it reports Developer Mode is missing or off, run:

```bash
./"Show Developer Mode.command"
```

There is also a button for this in the app, under Connect.

### 3. Turn Developer Mode on

On the phone, go to **Settings > Privacy & Security > Developer Mode**. It will be there now,
below "VPN & Device Management".

Turn it on. The phone restarts. After it boots, unlock it and tap **Turn On** at the prompt.

This is a one time setup. It persists across reboots, and you never do it again on that phone.

Automating the toggle itself is not possible: iOS refuses the AMFI enable action on any device
with a passcode set, which is every real phone.

### 4. Connect

Open http://127.0.0.1:8765 and hit **Connect**, or just relaunch the app, which auto connects
when it finds exactly one trusted phone.

The first connect downloads and mounts the Developer Disk Image, so it needs internet and takes
a few seconds. Every connect after that is instant.

### 5. Click the map

That is it.

## Daily use

Once set up, the whole routine is: plug in the unlocked phone, launch the app, click a saved
place. There is a `iPhone Location.app` bundle in the repo, so you can launch it from Spotlight
with Cmd+Space.

Two rules that always apply:

* **Keep the phone unlocked and plugged in.** iOS refuses these developer services to a locked
  device.
* **Leave the app running.** The simulated fix exists only while the app holds the connection
  open. Quit it, or unplug, and the real GPS returns immediately. This is by design and is also
  your undo button.

Do not run two copies at once. iOS allows a single client on these services, so the second one
just hangs.

## Modes

| Mode | What it does |
| --- | --- |
| **Teleport** | Click the map. The phone is there. |
| **Route** | Drop waypoints, or import a GPX file, then walk the line at a set speed. Supports loop and ping pong. |
| **Joystick** | Steer live with the on screen pad or W/A/S/D. Space stops. |

**Saved places** stores named locations for one click recall, plus a "Last used" button.
Everything persists in `~/.iphone-location-simulator/favorites.json`.

**GPS noise** adds a random offset of up to 20 metres to every fix, so a stationary position
drifts the way a real one does instead of sitting on a mathematically perfect point. The
readout shows both the intended point and the noisy fix actually sent.

Speed changes apply live, mid route. **Restore real GPS** calls `stopLocationSimulation` and
hands control straight back to the hardware.

## Tests

The geodesy, route engine, GPX parsing and jitter distribution are covered by tests that
need no device:

```bash
./.venv/bin/pip install -r requirements-dev.txt
./.venv/bin/python -m pytest tests/ -q
```

## Trying it without a phone

```bash
./run.sh --mock
```

Drives a fake device so you can explore the interface, routes, and the movement engine with
nothing plugged in. Nothing touches USB in this mode.

## What it touches, and what it sends where

There is no telemetry, no analytics, and no update check anywhere in this project or in
pymobiledevice3. Five external hosts are contacted:

| Host | When | What it learns |
| --- | --- | --- |
| `api.github.com`, `raw.githubusercontent.com` | first connect only | Downloads the ~15 MB personalized DDI from [doronz88/DeveloperDiskImage](https://github.com/doronz88/DeveloperDiskImage), a third party mirror rather than Apple. This is the one real trust decision in the stack. It is the same image Xcode ships, and Apple's signing server still has to approve it for your device, but the bytes come from GitHub. |
| `gs.apple.com` | first connect only | Apple's TSS signing server personalizes the DDI for your phone. It receives your device's ECID, ChipID and BoardID. This is the same exchange Xcode and iTunes perform. |
| `basemaps.cartocdn.com` | while the map is open | Map tiles, so it sees roughly where you are looking. Inherent to any web map. |
| `nominatim.openstreetmap.org` | only when you press Enter on a place name | Your typed query. Nothing is sent while you type, and raw coordinates never touch the network at all. |
| `pypi.org` | first install only | Package downloads. |

Leaflet is vendored in `static/vendor/` rather than loaded from a CDN, so no executable code is
fetched from a third party at runtime.

The local API binds `127.0.0.1` and validates the `Host` header against DNS rebinding, plus
`Sec-Fetch-Site` and `Origin` against cross site requests, because otherwise any page you
visited could quietly drive your phone's GPS. Requests carrying no browser headers at all are
allowed through, since a local process already has whatever access you do.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `PasswordProtected` or `GetProhibited` | The phone is locked. Unlock it and keep the screen awake. Setting Auto-Lock to Never helps. |
| Developer Mode missing from Settings | iOS is still hiding it. Run `./"Show Developer Mode.command"`. |
| Connect hangs forever at "Mounting" | The device side mounter has wedged, usually after an interrupted mount. Unplug and replug the cable. Restarting the phone always clears it. The app now gives up after three minutes and tells you this. |
| Connect hangs at any other stage | A leftover process is holding the service. `pkill -f spoofer.server` and try again. |
| `ModuleNotFoundError: No module named 'typer._click'` | You are on Python 3.9. This breaks the pymobiledevice3 CLI only, not this app. Install Python 3.10+, delete `.venv`, rerun. |

`./doctor.sh` walks every step of the chain and tells you exactly which one fails.

## Layout

| Path | Role |
| --- | --- |
| `spoofer/device.py` | Connection chain and the tick loop that pushes coordinates |
| `spoofer/geo.py` | Great circle maths, route cursor, GPX parsing, jitter |
| `spoofer/server.py` | JSON API, server sent events, the localhost security guard |
| `spoofer/favorites.py` | Persisted named locations |
| `spoofer/doctor.py` | Preflight diagnostics |
| `spoofer/reveal.py` | Un-hides the Developer Mode toggle via AMFI |
| `static/index.html` | Leaflet map UI |
| `iPhone Location.app` | Spotlight launchable wrapper |

The one architectural constraint worth knowing: the DTX channel has to stay open for the
simulated fix to hold. `LocationSession` keeps the tunnel, the DVT provider and the location
channel alive for the life of the session, and a background task re-asserts the current fix
every couple of seconds so CoreLocation never treats it as stale.

## Doing it without the UI

The whole project collapses to a couple of commands if you do not want a map. Needs Python
3.10+:

```bash
pipx install pymobiledevice3
pymobiledevice3 mounter auto-mount
pymobiledevice3 developer dvt simulate-location set -- 40.689247 -74.045843
```

That holds the location until you press enter. This project exists because doing it from a map,
with movement, is nicer.

## Credits

Effectively all of the hard work belongs to
[pymobiledevice3](https://github.com/doronz88/pymobiledevice3) by doronz88, which implements
Apple's device protocols from scratch. This project is a map and a movement engine bolted onto
its `LocationSimulation` service.

Map tiles by [CARTO](https://carto.com/attributions), data by
[OpenStreetMap](https://www.openstreetmap.org/copyright) contributors. Geocoding by
[Nominatim](https://nominatim.org/), whose
[usage policy](https://operations.osmfoundation.org/policies/nominatim/) applies if you fork
this: it is rate limited to one request per second and is not for heavy or bulk use.

## License

**GPL-3.0-or-later**, see [LICENSE](LICENSE) and [NOTICE](NOTICE).

The license is not a style choice. pymobiledevice3 is GPL-3.0-or-later, this project
imports it directly and does not function without it, so the combined work is copyleft.
Matching it is the honest and unambiguous option.

Vendored Leaflet is BSD 2-Clause, which is GPL compatible, see
`static/vendor/LEAFLET-LICENSE`.
