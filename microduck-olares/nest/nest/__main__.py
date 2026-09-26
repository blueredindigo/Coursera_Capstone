"""`python -m nest --config config.toml`, or `python -m nest --sim` to try it without robots."""

from __future__ import annotations

import argparse
import asyncio
import logging

from . import config as config_module
from .app import Nest


def main() -> None:
    parser = argparse.ArgumentParser(prog="nest", description=__doc__)
    parser.add_argument("--config", help="path to config.toml (default: two ducks on .local)")
    parser.add_argument("--sim", action="store_true", help="a simulated living room")
    parser.add_argument("--port", type=int, help="override the Pond's port")
    parser.add_argument("--eggs", action="store_true",
                        help="with --sim: the ducks start switched off and unhatched, as before "
                             "Christmas; switch them on from the Map tab")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = config_module.load(args.config)
    if args.sim and not cfg.landmarks:
        cfg.landmarks = dict(config_module.SIM_LANDMARKS)  # a furnished simulated room
    if args.port:
        cfg.web_port = args.port
    asyncio.run(run(cfg, args.sim, eggs=args.eggs))


async def run(cfg: config_module.Config, sim: bool, eggs: bool = False) -> None:
    import uvicorn

    from .web import build

    nest = Nest(cfg, sim=sim)
    if sim and eggs:
        nest.hatched.clear()
        for name in nest.world.ducks:
            nest.world.power(name, False)
    await nest.start()
    server = uvicorn.Server(uvicorn.Config(build(nest), host=cfg.web_host, port=cfg.web_port,
                                           log_level="warning"))
    try:
        await server.serve()
    finally:
        await nest.stop()


if __name__ == "__main__":
    main()
