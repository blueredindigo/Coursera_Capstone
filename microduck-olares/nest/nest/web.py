"""The Pond: a small web page served by the Nest on the LAN (and through LarePass's VPN).

Read-mostly: every duck's battery, tiredness, mood, needs and current behaviour; Reachy; the
messages the ducks have told each other; the battery diary. A few buttons: call a duck, pause
or resume it, bedtime and wake-up, log a pit stop, and a form to record what a duck saw (by hand
until the Phase 5 captioner does it from the camera).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .app import Nest


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

    def mind(name: str):
        if name not in nest.minds:
            raise HTTPException(404, f"no duck called {name!r}")
        return nest.minds[name]

    @app.get("/", response_class=HTMLResponse)
    async def page() -> str:
        return PAGE

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
            await m.client.stop()
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


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Pond</title>
<style>
:root{--bg:#f6f4ee;--card:#fff;--ink:#1f2328;--dim:#667085;--line:#e4e1d8;--accent:#c98a00;
--good:#2f7d4f;--warn:#b25e09;--bad:#b42318;--bar:#ece8dc}
@media (prefers-color-scheme:dark){:root{--bg:#15171a;--card:#1d2024;--ink:#e8e6e1;--dim:#9aa3ad;
--line:#2c3036;--accent:#e0a526;--good:#5cbf86;--warn:#f0a045;--bad:#f07167;--bar:#2a2e34}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}
header{padding:16px;display:flex;flex-wrap:wrap;gap:8px;align-items:center;justify-content:space-between}
h1{margin:0;font-size:20px}main{padding:0 16px 32px;display:grid;gap:16px;
grid-template-columns:repeat(auto-fit,minmax(300px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.card h2{margin:0 0 6px;font-size:17px;display:flex;justify-content:space-between;gap:8px}
.dim{color:var(--dim)}.pill{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
.row{display:flex;gap:8px;align-items:center;margin:4px 0}.row b{min-width:84px;font-weight:500}
.bar{flex:1;height:8px;background:var(--bar);border-radius:99px;overflow:hidden}
.bar i{display:block;height:100%;background:var(--accent)}
.battery{font-size:28px;font-weight:600}.band-high{color:var(--good)}.band-medium{color:var(--ink)}
.band-low{color:var(--warn)}.band-very_low{color:var(--bad)}.band-charging{color:var(--accent)}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);
border-radius:8px;padding:6px 10px;cursor:pointer}button:hover{border-color:var(--accent)}
input,select{font:inherit;padding:5px 8px;border-radius:8px;border:1px solid var(--line);
background:var(--bg);color:var(--ink);min-width:0}
form{display:flex;flex-wrap:wrap;gap:6px;margin-top:6px}
ul{margin:6px 0 0;padding-left:18px}li{margin:2px 0}.msg{font-style:italic}
</style></head><body>
<header><h1>The Pond</h1><div class="row">
<span id="reachy" class="pill">Reachy: …</span>
<button onclick="post('/api/wake')">Good morning</button>
<button onclick="post('/api/bedtime')">Bedtime</button></div></header>
<main id="ducks"></main>
<main>
<section class="card"><h2>What they told each other</h2><ul id="messages"></ul>
<p class="dim" id="pending"></p></section>
<section class="card"><h2>A duck saw something</h2>
<form onsubmit="event.preventDefault();sighting(this)">
<select name="duck"></select><input name="obj" placeholder="yellow ball" required size="10">
<select name="relation"><option>behind</option><option>under</option><option>on</option>
<option>next to</option><option>in front of</option><option>near</option></select>
<input name="landmark" placeholder="green chair" required size="10">
<input name="near" placeholder="near… (sofa)" size="8"><button>Record</button></form>
<form onsubmit="event.preventDefault();ask(this)"><select name="duck"></select>
<input name="obj" placeholder="where is… (yellow ball)" required size="14"><button>Ask</button></form>
<p id="answer" class="dim"></p></section>
<section class="card"><h2>Batteries</h2><div id="packs"></div>
<form onsubmit="event.preventDefault();pitstop(this)"><select name="duck"></select>
<select name="pack"><option>A</option><option>B</option><option>C</option><option>D</option></select>
<button>Log pit stop</button></form></section>
<section class="card"><h2>Feed</h2><ul id="feed"></ul></section>
</main>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
async function post(url,body){const r=await fetch(url,{method:'POST',headers:{'content-type':'application/json'},
body:body?JSON.stringify(body):undefined});return r.json();}
function form(f){return Object.fromEntries(new FormData(f).entries());}
async function sighting(f){const b=form(f);if(!b.near)delete b.near;const r=await post('/api/sighting',b);
$('answer').textContent=`${b.duck}: ${r.meal}${r.queued_for_friend?' — will tell its friend: '+r.queued_for_friend:''}`;}
async function ask(f){const r=await post('/api/ask',form(f));$('answer').textContent=r.known?
`${r.where} (${r.told_by?'heard from '+r.told_by:'saw it'}; ${Math.round(r.confidence*100)}% sure)`:
(r.asked_friend?'Doesn\\'t know. Asked its friend.':'Doesn\\'t know.');}
async function pitstop(f){await post('/api/pitstop',form(f));}
const WORDS={high:'Bright and chirpy',medium:'Normal',low:'Getting tired',very_low:'Very tired: going to bed',
charging:'Napping on the charger',unknown:'Battery not known yet'};
function duckCard(name,d){const needs=Object.entries(d.needs).map(([k,v])=>
`<div class="row"><b>${esc(k)}</b><span class="bar"><i style="width:${Math.round(v*100)}%"></i></span></div>`).join('');
const ev=d.events.slice().reverse().map(e=>`<li>${esc(e.text)}</li>`).join('');
return `<section class="card"><h2><span>${esc(name)}</span><span class="pill">${d.connected?'connected':esc(d.transport)}</span></h2>
<div class="dim">${esc(d.personality)}</div>
<div class="row"><span class="battery band-${d.tiredness}">${d.battery_percent==null?'–':Math.round(d.battery_percent)+'%'}</span>
<span>${WORDS[d.tiredness]||d.tiredness}${d.pack?' · pack '+esc(d.pack):''}</span></div>
<div class="row"><b>mood</b>${esc(d.mood)}</div><div class="row"><b>doing</b>${esc((d.behaviour||'—').replace(/_/g,' '))}${d.paused?' (paused)':''}</div>
${d.safety?`<div class="row dim"><b>safety</b>${esc(d.safety)}</div>`:''}
<div class="dim">needs (urge)</div>${needs}
<div class="row dim"><b>Duckdex</b>${d.duckdex} thing${d.duckdex===1?'':'s'} · knows: ${esc(d.knows.join(', ')||'nothing yet')}</div>
<div class="row"><button onclick="post('/api/duck/${name}/call')">Call</button>
<button onclick="post('/api/duck/${name}/pause')">${d.paused?'Resume':'Pause'}</button>
<button onclick="post('/api/duck/${name}/roll-call')" title="Tell the two ducks apart in Reachy's view">Roll call</button></div>
<ul>${ev}</ul></section>`;}
let names=[];
async function refresh(){try{const s=await (await fetch('/api/status')).json();
$('reachy').textContent=`Reachy: ${s.reachy.status}${s.reachy.watching?' · watching '+s.reachy.watching:''}${s.sim?' · SIMULATION':''}`;
$('ducks').innerHTML=Object.entries(s.ducks).map(([n,d])=>duckCard(n,d)).join('');
const n=Object.keys(s.ducks);if(n.join()!==names.join()){names=n;document.querySelectorAll('select[name=duck]')
.forEach(sel=>sel.innerHTML=n.map(x=>`<option>${esc(x)}</option>`).join(''));}
$('messages').innerHTML=s.messages.slice().reverse().map(m=>`<li><b>${esc(m.speaker)} → ${esc(m.listener)}</b>
<span class="msg">“${esc(m.subtitle)}”</span> <span class="dim">(${esc(m.chirps.join(' '))}; ${m.bytes} bytes)</span></li>`).join('')||'<li class="dim">nothing yet</li>';
$('pending').textContent=s.pending_messages?`${s.pending_messages} message(s) waiting until the ducks are within earshot`:'';
$('packs').innerHTML=Object.entries(s.batteries).map(([p,b])=>`<div class="row"><b>Pack ${p}</b>
${b.in?'in '+esc(b.in):'spare'} · ${b.runs} runs${b.drain_pct_per_hour?' · '+b.drain_pct_per_hour+'%/h':''}${b.getting_tired?' · <span class="band-low">getting tired</span>':''}</div>`).join('');
$('feed').innerHTML=s.feed.slice().reverse().map(e=>`<li><b>${esc(e.who)}</b> ${esc(e.text)}</li>`).join('');
}catch(e){$('reachy').textContent='The Nest is not answering';}}
refresh();setInterval(refresh,1500);
</script></body></html>
"""
