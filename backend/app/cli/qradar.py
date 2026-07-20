"""Register and inspect monitored QRadar consoles.

    python -m app.cli.qradar add --name qradarce2 \
        --url https://192.168.122.50 \
        --token-file .secrets/qradar.sec \
        --ca-file .secrets/qradar-ca.pem

Running `add` twice updates the existing instance rather than creating a
duplicate.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.database import get_sessionmaker
from app.providers.factory import build_provider_for_instance
from app.services.instance_registry import (
    InstanceNotFound,
    InstanceRegistry,
    read_token_file,
)


async def _add(args: argparse.Namespace) -> int:
    # Read before opening a transaction: a bad path should fail before we touch
    # the database, not halfway through.
    token = read_token_file(args.token_file) if args.token_file else None

    maker = get_sessionmaker()
    async with maker() as session:
        registry = InstanceRegistry(session)
        result = await registry.register(
            name=args.name,
            console_host=args.url.rstrip("/"),
            sec_token=token,
            api_version=args.api_version,
            ca_bundle_path=args.ca_file,
            provider_kind=args.provider,
            description=args.description,
            mcp_base_url=args.mcp_url,
        )
        instance = result.instance

        verified = "skipped"
        if args.verify:
            provider = build_provider_for_instance(instance)
            try:
                info = await provider.validate_connection()
                instance.qradar_version = info.version
                verified = f"ok (QRadar {info.version})"
            except Exception as exc:
                # Registration still commits: a console that is briefly
                # unreachable should not be impossible to register.
                verified = f"FAILED ({type(exc).__name__})"
            finally:
                await provider.aclose()

        await session.commit()

    print(f"instance {result.action}: {instance.name}")
    print(f"  id           {instance.id}")
    print(f"  console      {instance.console_host}")
    print(f"  api version  {instance.api_version}")
    print(f"  provider     {instance.provider_kind}")
    print(f"  verify_ssl   {instance.verify_ssl}")
    print(f"  ca bundle    {instance.ca_bundle_path or '(system trust store)'}")
    print(f"  token        {'stored (encrypted)' if instance.sec_token else 'NOT SET'}")
    print(f"  connection   {verified}")
    return 0 if not verified.startswith("FAILED") else 1


async def _list(_: argparse.Namespace) -> int:
    maker = get_sessionmaker()
    async with maker() as session:
        instances = await InstanceRegistry(session).list_all()

    if not instances:
        print("no QRadar instances registered")
        return 0
    print(f"{'NAME':<20} {'CONSOLE':<32} {'API':<6} {'PROVIDER':<9} {'STATUS':<10} ENABLED")
    for i in instances:
        print(
            f"{i.name:<20} {i.console_host:<32} {i.api_version:<6} "
            f"{i.provider_kind:<9} {i.status!s:<10} {i.enabled}"
        )
    return 0


async def _test(args: argparse.Namespace) -> int:
    maker = get_sessionmaker()
    async with maker() as session:
        instance = await InstanceRegistry(session).get_by_name(args.name)
        provider = build_provider_for_instance(instance)
        try:
            info = await provider.validate_connection()
        finally:
            await provider.aclose()

    print(f"{instance.name}: reachable={info.reachable} version={info.version} build={info.build}")
    return 0 if info.reachable else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli.qradar")
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="register a console (idempotent by name)")
    add.add_argument("--name", required=True)
    add.add_argument("--url", required=True, help="https:// console base URL")
    add.add_argument(
        "--token-file",
        help="path to a file holding the SEC token. Never pass the token itself: "
        "an argv element is visible in `ps` and in shell history.",
    )
    add.add_argument("--ca-file", help="CA bundle trusted for this console")
    add.add_argument("--api-version", default="20.0")
    add.add_argument("--provider", default="rest", choices=["rest", "mcp", "mock"])
    add.add_argument("--mcp-url", default=None)
    add.add_argument("--description", default=None)
    add.add_argument(
        "--no-verify",
        dest="verify",
        action="store_false",
        help="skip the post-registration connection check",
    )
    add.set_defaults(func=_add, verify=True)

    lst = sub.add_parser("list", help="list registered consoles")
    lst.set_defaults(func=_list)

    test = sub.add_parser("test", help="check connectivity to a registered console")
    test.add_argument("--name", required=True)
    test.set_defaults(func=_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(args.func(args))
    except (InstanceNotFound, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
