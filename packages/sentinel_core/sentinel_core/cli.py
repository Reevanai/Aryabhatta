"""Administration commands: migrate, create-tenant, create-branch, audit-anchor."""

import argparse
import asyncio
import sys
from importlib import resources
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sentinel_core.audit import anchor_chain, record_audit
from sentinel_core.config import Settings, get_settings
from sentinel_core.context import TenantContext
from sentinel_core.db import apply_context
from sentinel_core.ids import uuid7
from sentinel_core.provisioning import NewTenant, create_tenant
from sentinel_core.runtime import build_key_service, build_platform


def _owner_url(settings: Settings) -> str:
    if not settings.database_url_owner:
        raise SystemExit("SENTINEL_DATABASE_URL_OWNER is required for this command")
    return settings.database_url_owner


def migrate(owner_url: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(resources.files("sentinel_core") / "migrations"))
    config.set_main_option("sqlalchemy.url", owner_url.replace("%", "%%"))
    command.upgrade(config, "head")


async def _create_tenant(settings: Settings, args: argparse.Namespace) -> None:
    engine = create_async_engine(_owner_url(settings))
    try:
        tenant_id = await create_tenant(
            engine,
            build_key_service(settings),
            NewTenant(
                name=args.name,
                institution_type=args.institution_type,
                oidc_issuer=args.issuer,
                jwks_uri=args.jwks_uri,
                tenant_id=UUID(args.id) if args.id else None,
            ),
        )
    finally:
        await engine.dispose()
    print(tenant_id)


async def _create_branch(settings: Settings, args: argparse.Namespace) -> None:
    engine = create_async_engine(_owner_url(settings))
    ctx = TenantContext(UUID(args.tenant), "provisioning")
    try:
        async with engine.connect() as conn, conn.begin():
            await apply_context(conn, ctx)
            row = (
                await conn.execute(
                    text(
                        "INSERT INTO core.branch (id, tenant_id, code, name) VALUES (:id, :t, :c, :n) "
                        "ON CONFLICT (tenant_id, code) DO NOTHING RETURNING id"
                    ),
                    {"id": uuid7(), "t": ctx.tenant_id, "c": args.code, "n": args.name},
                )
            ).first()
            if row:
                await record_audit(
                    conn,
                    ctx,
                    action="branch.create",
                    resource=f"branch:{row.id}",
                    details={"code": args.code},
                )
    finally:
        await engine.dispose()


async def _audit_anchor(settings: Settings, args: argparse.Namespace) -> None:
    platform = build_platform(settings)
    ctx = TenantContext(UUID(args.tenant), "audit-anchor")
    try:
        key = await platform.keys.get(ctx, "audit")
        async with platform.db.tenant_tx(ctx) as conn:
            sequence = await anchor_chain(conn, ctx, key.key)
    finally:
        await platform.aclose()
    print(sequence if sequence is not None else "nothing to anchor")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sentinel-admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrate", help="apply database migrations as the owner role")
    tenant = sub.add_parser("create-tenant")
    tenant.add_argument("--name", required=True)
    tenant.add_argument("--institution-type", default="urban_cooperative_bank")
    tenant.add_argument("--issuer", required=True)
    tenant.add_argument("--jwks-uri", required=True)
    tenant.add_argument("--id")
    branch = sub.add_parser("create-branch")
    branch.add_argument("--tenant", required=True)
    branch.add_argument("--code", required=True)
    branch.add_argument("--name", required=True)
    anchor = sub.add_parser("audit-anchor", help="sign and store the current audit chain head")
    anchor.add_argument("--tenant", required=True)
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])
    settings = get_settings()
    if args.cmd == "migrate":
        migrate(_owner_url(settings))
    elif args.cmd == "create-tenant":
        asyncio.run(_create_tenant(settings, args))
    elif args.cmd == "create-branch":
        asyncio.run(_create_branch(settings, args))
    else:
        asyncio.run(_audit_anchor(settings, args))
