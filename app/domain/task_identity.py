from typing import Any, Optional


def build_owner_key(tenant: Any, database: Any, odoo_uid: Any) -> str:
    """Build a deterministic, canonical owner key from verified tenant, database, and odoo_uid.
    Normalizes protocol, trailing slashes, whitespace, and types.
    Returns empty string if any required component is missing.
    """
    clean_tenant = str(tenant or "").strip()
    if not clean_tenant:
        return ""
    if "://" in clean_tenant:
        clean_tenant = clean_tenant.split("://", 1)[1]
    clean_tenant = clean_tenant.rstrip("/")

    clean_db = str(database or "").strip()
    if not clean_db:
        return ""

    clean_uid = str(odoo_uid or "").strip()
    if not clean_uid:
        return ""

    return f"{clean_tenant}:{clean_db}:{clean_uid}"
