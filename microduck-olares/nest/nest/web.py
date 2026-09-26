"""The Pond: the web page for the things you ask the ducks to do.

Served by the Nest on the LAN (and through LarePass's VPN) at `http://<jetson>:8090`. The page
itself is `static/pond.html`, built from the Claude Design canvas "The Pond: Duck Care Controls":
an 8-bit pond where Ah-Ah and Tee-Tee walk about and show their moods, then three tabs:

* **Care:** each duck's energy, mood and what it's doing, and six things to ask it;
* **Saw & find:** record what a duck saw as a sentence, and ask where something is;
* **Night:** bedtime and good morning, and pit stops with the battery diary.

Everything the page needs is served from here, fonts included: it works with no internet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .app import Nest

STATIC = Path(__file__).parent / "static"


class SightingIn(BaseModel):
    duck: str
    obj: str
    relation: str = "near"
    landmark: str
    near: str | None = None


class PitStopIn(BaseModel):
    duck: str
    pack: str


class AskIn(BaseModel):
    duck: str
    obj: str


def build(nest: Nest) -> FastAPI:
    app = FastAPI(title="The Pond", docs_url="/docs")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def mind(name: str):
        if name not in nest.minds:
            raise HTTPException(404, f"no duck called {name!r}")
        return nest.minds[name]

    @app.get("/")
    async def page() -> FileResponse:
        return FileResponse(STATIC / "pond.html", headers={"Cache-Control": "no-cache"})

    @app.post("/api/duck/{name}/do/{action}")
    async def do(name: str, action: str) -> dict[str, Any]:
        mind(name)
        ok, message = await nest.direct(name, action)
        return {"ok": ok, "message": message}

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return nest.status()

    @app.post("/api/duck/{name}/call")
    async def call(name: str) -> dict[str, str]:
        mind(name)
        await nest.call(name)
        return {"ok": "called"}

    @app.post("/api/duck/{name}/pause")
    async def pause(name: str) -> dict[str, bool]:
        m = mind(name)
        m.paused = not m.paused
        if m.paused:
            m.interrupt()
            try:
                await m.client.stop()
            except Exception:
                pass  # not connected: it isn't moving anyway
        nest._event(name, "paused by you" if m.paused else "back to its own life")
        return {"paused": m.paused}

    @app.post("/api/duck/{name}/roll-call")
    async def roll_call(name: str) -> dict[str, bool]:
        mind(name)
        return {"resolved": await nest.roll_call(name)}

    @app.post("/api/sighting")
    async def sighting(body: SightingIn) -> dict[str, Any]:
        mind(body.duck)
        return nest.record_sighting(body.duck, body.obj, body.relation, body.landmark, body.near)

    @app.post("/api/ask")
    async def ask(body: AskIn) -> dict[str, Any]:
        mind(body.duck)
        answer = nest.where_is(body.duck, body.obj)
        if not answer["known"]:
            asked = nest.tell_friend(body.duck, "ask", body.obj)
            answer["asked_friend"] = asked
        return answer

    @app.post("/api/pitstop")
    async def pitstop(body: PitStopIn) -> dict[str, Any]:
        mind(body.duck)
        try:
            nest.batteries.pit_stop(body.duck, body.pack)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        nest._event(body.duck, f"pit stop: pack {body.pack.upper()} fitted")
        return {"fitted": nest.batteries.fitted}

    @app.post("/api/bedtime")
    async def bedtime() -> dict[str, str]:
        await nest.bedtime()
        return {"ok": "goodnight"}

    @app.post("/api/wake")
    async def wake() -> dict[str, str]:
        await nest.wake()
        return {"ok": "good morning"}

    return app


