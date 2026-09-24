import argparse
import hashlib
import secrets

from app.db import SessionLocal
from app.models import ApiCredential, Tenant
from app.services import ALL_SCOPES


def main() -> None:
    parser = argparse.ArgumentParser(prog="wg-service")
    commands = parser.add_subparsers(dest="command", required=True)
    tenant_cmd = commands.add_parser("create-tenant")
    tenant_cmd.add_argument("tenant_id")
    tenant_cmd.add_argument("name")
    key_cmd = commands.add_parser("issue-key")
    key_cmd.add_argument("tenant_id")
    key_cmd.add_argument("name")
    key_cmd.add_argument("--scopes", nargs="+", required=True)
    commands.add_parser("scopes")
    args = parser.parse_args()

    if args.command == "scopes":
        print("\n".join(sorted(ALL_SCOPES)))
        return
    with SessionLocal() as db:
        if args.command == "create-tenant":
            if db.get(Tenant, args.tenant_id):
                parser.error("Tenant already exists")
            db.add(Tenant(id=args.tenant_id, name=args.name))
            db.commit()
            print(args.tenant_id)
            return
        tenant = db.get(Tenant, args.tenant_id)
        if not tenant:
            parser.error("Tenant does not exist")
        invalid = set(args.scopes) - ALL_SCOPES
        if invalid:
            parser.error(f"Unknown scopes: {', '.join(sorted(invalid))}")
        secret = f"wg_live_{secrets.token_urlsafe(36)}"
        db.add(ApiCredential(tenant_id=tenant.id, name=args.name,
                             key_hash=hashlib.sha256(secret.encode()).hexdigest(),
                             scopes=",".join(sorted(set(args.scopes))), active=True))
        db.commit()
        print(secret)


if __name__ == "__main__":
    main()
