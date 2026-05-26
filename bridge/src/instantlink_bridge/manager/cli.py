"""Command line entry point for the Bridge management scaffold."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from collections.abc import Sequence
from contextlib import suppress
from pathlib import Path

from aiohttp import web

from instantlink_bridge.config import DEFAULT_CONFIG_PATH
from instantlink_bridge.manager.api import RequestIdFactory, create_app
from instantlink_bridge.manager.auth import ClientStore, SignedRequestVerifier
from instantlink_bridge.manager.contract import (
    DEFAULT_BIND_HOSTS,
    DEFAULT_PORT,
    JsonObject,
    new_request_id,
    routes_payload,
    success_response,
)
from instantlink_bridge.manager.status import (
    collect_hello_payload,
    collect_status_payload,
)

LOGGER = logging.getLogger(__name__)


def main(
    argv: Sequence[str] | None = None,
    *,
    request_id_factory: RequestIdFactory = new_request_id,
) -> None:
    """Run the manager CLI."""

    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "hello":
        emit_json(
            success_response(
                collect_hello_payload(args.config),
                request_id=request_id_factory(),
            )
        )
        return
    if args.command == "status":
        emit_json(
            success_response(
                collect_status_payload(args.config),
                request_id=request_id_factory(),
            )
        )
        return
    if args.command == "api-routes":
        emit_json(success_response(routes_payload(), request_id=request_id_factory()))
        return
    if args.command == "serve":
        configure_logging(args.log_level)
        hosts = tuple(args.host) if args.host else DEFAULT_BIND_HOSTS
        validate_bind_hosts(hosts, allow_unsafe=args.allow_unsafe_bind)
        asyncio.run(
            serve(
                config_path=args.config,
                hosts=hosts,
                port=args.port,
                clients_dir=args.clients_dir,
                request_id_factory=request_id_factory,
            )
        )
        return
    parser.error("missing command")


def build_parser() -> argparse.ArgumentParser:
    """Build the manager CLI parser."""

    parser = argparse.ArgumentParser(description="InstantLink Bridge management API")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"config file path (default: {DEFAULT_CONFIG_PATH})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_json_command(subparsers, "hello", "emit discovery metadata")
    add_json_command(subparsers, "status", "emit local read-only status")
    add_json_command(subparsers, "api-routes", "emit the management route catalog")

    serve_parser = subparsers.add_parser("serve", help="run the HTTP management API")
    serve_parser.add_argument(
        "--host",
        action="append",
        help="host/IP address to bind; may be provided more than once",
    )
    serve_parser.add_argument(
        "--allow-unsafe-bind",
        action="store_true",
        help="allow wildcard/non-Bridge management binds for development only",
    )
    serve_parser.add_argument(
        "--clients-dir",
        type=Path,
        default=Path("/var/lib/InstantLinkBridge/management/clients"),
        help="management client record directory",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port to bind (default: {DEFAULT_PORT})",
    )
    serve_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Python logging level",
    )
    return parser


def validate_bind_hosts(hosts: Sequence[str], *, allow_unsafe: bool = False) -> None:
    """Reject wildcard management binds unless explicitly requested for development."""

    if allow_unsafe:
        return
    for host in hosts:
        if host.strip() in {"", "0.0.0.0", "::", "*"}:
            raise SystemExit(
                "Refusing unsafe management bind. Use --allow-unsafe-bind only for development."
            )


def add_json_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    name: str,
    help_text: str,
) -> None:
    """Add a subcommand whose output is JSON by contract."""

    command = subparsers.add_parser(name, help=help_text)
    command.add_argument(
        "--json",
        action="store_true",
        help="emit JSON; accepted for CLI parity and currently always enabled",
    )


def emit_json(payload: JsonObject) -> None:
    """Print stable compact JSON to stdout."""

    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def configure_logging(log_level: str) -> None:
    """Configure process logging for the HTTP server."""

    logging.basicConfig(
        level=getattr(logging, log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


async def serve(
    *,
    config_path: Path,
    hosts: Sequence[str],
    port: int,
    clients_dir: Path,
    request_id_factory: RequestIdFactory = new_request_id,
) -> None:
    """Run the aiohttp manager app until SIGINT or SIGTERM."""

    auth_verifier = SignedRequestVerifier(ClientStore(clients_dir))
    app = create_app(
        config_path=config_path,
        request_id_factory=request_id_factory,
        auth_verifier=auth_verifier,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    stop_event = asyncio.Event()
    install_signal_handlers(stop_event)
    try:
        started = 0
        for host in hosts:
            site = web.TCPSite(runner, host, port)
            try:
                await site.start()
            except OSError:
                LOGGER.warning("manager.bind_failed host=%s port=%s", host, port, exc_info=True)
                continue
            started += 1
            LOGGER.info("manager.listening host=%s port=%s", host, port)
        if started == 0:
            raise RuntimeError("manager could not bind any configured host")
        await stop_event.wait()
    finally:
        await runner.cleanup()


def install_signal_handlers(stop_event: asyncio.Event) -> None:
    """Install cross-platform process-stop signal handlers."""

    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(signum, stop_event.set)
            continue
        signal.signal(signum, lambda _signum, _frame: stop_event.set())


if __name__ == "__main__":
    main()
