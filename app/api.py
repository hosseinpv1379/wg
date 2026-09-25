import hashlib
import secrets
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import ApiCredential, Tenant
from app.services import ALL_SCOPES


def get_tenant(x_api_key: Annotated[str | None, Header()] = None,
               db: Session = Depends(get_db)) -> tuple[str, set[str], str]:
    if not x_api_key:
        raise HTTPException(401, detail={"code": "AUTH_REQUIRED", "message": "X-API-Key is required"})
    digest = hashlib.sha256(x_api_key.encode()).hexdigest()
    credential = db.scalar(select(ApiCredential).where(ApiCredential.key_hash == digest,
                                                        ApiCredential.active.is_(True)))
    if credential:
        return credential.tenant_id, set(credential.scopes.split(",")), credential.id
    bootstrap = settings.bootstrap_api_key
    if bootstrap and secrets.compare_digest(x_api_key, bootstrap):
        tenant_id = settings.bootstrap_tenant_id
        if not db.get(Tenant, tenant_id):
            db.add(Tenant(id=tenant_id, name="Bootstrap tenant"))
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                if not db.get(Tenant, tenant_id):
                    raise
        return tenant_id, ALL_SCOPES, "bootstrap"
    raise HTTPException(401, detail={"code": "INVALID_API_KEY", "message": "API key is invalid"})


def require_scope(scope: str):
    def dependency(auth: tuple[str, set[str], str] = Depends(get_tenant)) -> tuple[str, str]:
        tenant_id, scopes, credential_id = auth
        if scope not in scopes:
            raise HTTPException(403, detail={"code": "INSUFFICIENT_SCOPE", "message": f"Missing scope: {scope}"})
        return tenant_id, credential_id
    return dependency
