"""HTTP front end: serves the map UI and exposes the session over a small JSON API."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import favorites, geo
from .device import DeviceError, LocationSession

log = logging.getLogger("spoofer.server")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# A page you visit in a browser can POST to 127.0.0.1 without your knowledge, and this API
# moves a physical phone's reported location and reports its UDID. Loopback is not a trust
# boundary on its own, so the guard below checks Host (against DNS rebinding) and
# Sec-Fetch-Site/Origin (against cross-site requests).
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
MAX_GPX_BYTES = 4 * 1024 * 1024

# Extended at startup when the operator deliberately binds a non-loopback interface.
allowed_hosts: set[str] = set(LOOPBACK_HOSTS)

session = LocationSession()


auto_connect = True


async def _auto_connect() -> None:
    """Connect on launch when exactly one phone is attached, so opening the app is enough."""
    await asyncio.sleep(0.4)  # let uvicorn finish binding before touching USB
    try:
        devices = await LocationSession.list_attached()
    except Exception:
        return
    usable = [d for d in devices if d.get("paired") and d.get("developer_mode") is not False]
    if len(usable) != 1:
        return
    try:
        log.info("auto-connecting to %s", usable[0].get("name") or usable[0]["udid"])
        await session.connect(usable[0]["udid"])
    except Exception as exc:
        # Not an error worth shouting about — the UI shows the reason and a Connect button.
        log.info("auto-connect skipped: %s", exc)


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    task = asyncio.create_task(_auto_connect()) if auto_connect and not session.mock else None
    yield
    if task and not task.done():
        task.cancel()
    # Drop the simulated fix and close the tunnel so the phone reverts on Ctrl-C.
    await session.disconnect()


app = FastAPI(title="iPhone Location Simulator", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.exception_handler(DeviceError)
async def _device_error_handler(_: Request, exc: DeviceError) -> JSONResponse:
    return JSONResponse({"error": exc.message, "hint": exc.hint}, status_code=409)


def _host_name(header: Optional[str]) -> str:
    """Strip the port and any IPv6 brackets off a Host/Origin authority."""
    if not header:
        return ""
    authority = header.rsplit("/", 1)[-1]
    if authority.startswith("["):  # [::1]:8765
        return authority[1:].split("]", 1)[0]
    return authority.rsplit(":", 1)[0] if ":" in authority else authority


@app.middleware("http")
async def guard(request: Request, call_next):
    """Reject DNS-rebinding and cross-site requests before they reach a handler."""
    if _host_name(request.headers.get("host")) not in allowed_hosts:
        return JSONResponse(
            {"error": "Refused: unexpected Host header.", "hint": "Reach this server as localhost."},
            status_code=403,
        )

    # Every current browser sends Sec-Fetch-Site. Its absence means a non-browser client
    # (curl, a script), which already has whatever access the user has — nothing to protect
    # against there. Its presence with any value but same-origin is a cross-site request.
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return JSONResponse({"error": "Refused: cross-site request."}, status_code=403)

    origin = request.headers.get("origin")
    if origin and _host_name(origin) not in allowed_hosts:
        return JSONResponse({"error": "Refused: cross-origin request."}, status_code=403)

    return await call_next(request)


async def _read_limited(request: Request, limit: int) -> bytes:
    """Read a request body, refusing anything over `limit` without buffering it all first."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(413, f"body too large (limit {limit // 1024 // 1024} MB)")
    total, chunks = 0, []
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise HTTPException(413, f"body too large (limit {limit // 1024 // 1024} MB)")
        chunks.append(chunk)
    return b"".join(chunks)


async def _body(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _coord(data: dict[str, Any]) -> tuple[float, float]:
    try:
        lat, lon = float(data["lat"]), float(data["lon"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "lat and lon are required numbers")
    if not geo.looks_like_coord(lat, lon):
        raise HTTPException(400, "coordinates out of range")
    return lat, lon


@app.get("/api/devices")
async def devices() -> dict[str, Any]:
    if session.mock:
        return {
            "devices": [
                {
                    "udid": "MOCK",
                    "connection": "Mock",
                    "name": "Mock iPhone (no device touched)",
                    "ios": "26.0",
                    "model": "iPhone17,1",
                    "developer_mode": True,
                    "paired": True,
                }
            ]
        }
    return {"devices": await LocationSession.list_attached()}


@app.get("/api/state")
async def state() -> dict[str, Any]:
    return session.snapshot()


@app.get("/api/events")
async def events() -> StreamingResponse:
    """Server-sent events carrying the session snapshot on every change."""
    queue: asyncio.Queue[None] = asyncio.Queue(maxsize=1)

    def on_change() -> None:
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait(None)

    unsubscribe = session.subscribe(on_change)

    async def stream():
        try:
            yield f"data: {json.dumps(session.snapshot())}\n\n"
            while True:
                try:
                    await asyncio.wait_for(queue.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass  # heartbeat, also keeps proxies from closing the stream
                yield f"data: {json.dumps(session.snapshot())}\n\n"
        finally:
            unsubscribe()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/connect")
async def connect(request: Request) -> dict[str, Any]:
    data = await _body(request)
    udid: Optional[str] = data.get("udid") or None
    await session.connect(udid)
    return session.snapshot()


@app.post("/api/reveal-developer-mode")
async def reveal_developer_mode(request: Request) -> dict[str, Any]:
    data = await _body(request)
    if session.mock:
        return {"revealed": True, "already_enabled": False}
    return await LocationSession.reveal_developer_mode(data.get("udid") or None)


@app.post("/api/disconnect")
async def disconnect() -> dict[str, Any]:
    await session.disconnect()
    return session.snapshot()


@app.post("/api/teleport")
async def teleport(request: Request) -> dict[str, Any]:
    lat, lon = _coord(await _body(request))
    await session.teleport(lat, lon)
    return session.snapshot()


@app.post("/api/route")
async def route(request: Request) -> dict[str, Any]:
    data = await _body(request)
    raw = data.get("points") or []
    points: list[tuple[float, float]] = []
    for p in raw:
        try:
            lat, lon = float(p[0]), float(p[1])
        except (TypeError, ValueError, IndexError):
            raise HTTPException(400, "points must be [[lat, lon], …]")
        if not geo.looks_like_coord(lat, lon):
            raise HTTPException(400, "a point is out of range")
        points.append((lat, lon))
    if len(points) < 2:
        raise HTTPException(400, "a route needs at least two points")
    await session.start_route(
        points,
        speed_kmh=float(data.get("speed_kmh") or 5.0),
        loop=bool(data.get("loop")),
        ping_pong=bool(data.get("ping_pong")),
    )
    return session.snapshot()


@app.post("/api/gpx")
async def gpx(request: Request) -> dict[str, Any]:
    """Turn an uploaded GPX file into a route without starting it."""
    text = (await _read_limited(request, MAX_GPX_BYTES)).decode("utf-8", "replace")
    try:
        points = geo.parse_gpx(text)
    except Exception as exc:
        raise HTTPException(400, f"could not parse GPX: {exc}")
    return {"points": [list(p) for p in points]}


@app.post("/api/joystick")
async def joystick(request: Request) -> dict[str, Any]:
    data = await _body(request)
    try:
        heading = float(data["heading"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "heading is required")
    await session.joystick(heading, float(data.get("speed_kmh") or 5.0))
    return session.snapshot()


@app.post("/api/stop")
async def stop() -> dict[str, Any]:
    await session.stop_moving()
    return session.snapshot()


@app.post("/api/pause")
async def pause(request: Request) -> dict[str, Any]:
    data = await _body(request)
    session.set_paused(bool(data.get("paused", True)))
    return session.snapshot()


@app.post("/api/speed")
async def speed(request: Request) -> dict[str, Any]:
    data = await _body(request)
    session.set_speed(float(data.get("speed_kmh") or 0.0))
    return session.snapshot()


@app.post("/api/jitter")
async def jitter(request: Request) -> dict[str, Any]:
    data = await _body(request)
    session.set_jitter(float(data.get("jitter_m") or 0.0))
    return session.snapshot()


@app.get("/api/favorites")
async def get_favorites() -> dict[str, Any]:
    return favorites.load()


@app.post("/api/favorites")
async def add_favorite(request: Request) -> dict[str, Any]:
    data = await _body(request)
    lat, lon = _coord(data)
    return favorites.add(str(data.get("name") or ""), lat, lon)


@app.post("/api/favorites/delete")
async def delete_favorite(request: Request) -> dict[str, Any]:
    data = await _body(request)
    name = str(data.get("name") or "")
    if not name:
        raise HTTPException(400, "name is required")
    return favorites.remove(name)


@app.post("/api/clear")
async def clear() -> dict[str, Any]:
    await session.clear()
    return session.snapshot()


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="iPhone location simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Drive a fake device instead of a real one, for trying out the UI.",
    )
    parser.add_argument(
        "--no-auto-connect",
        action="store_true",
        help="Don't connect automatically on launch; wait for the Connect button.",
    )
    args = parser.parse_args()
    session.mock = args.mock

    global auto_connect
    auto_connect = not args.no_auto_connect

    if _host_name(args.host) not in LOOPBACK_HOSTS:
        # Binding off-loopback puts an unauthenticated device-control API on the network.
        allowed_hosts.add(_host_name(args.host))
        print(
            f"  WARNING: binding {args.host} exposes this API to your network with no "
            "authentication. Anyone who can reach it can move your phone's location.",
            flush=True,
        )

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    banner = "  Map UI → http://%s:%d" % (args.host, args.port)
    if args.mock:
        banner += "   [MOCK — no device is touched]"
    print("\n" + banner + "\n", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
