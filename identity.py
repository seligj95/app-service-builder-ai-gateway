"""Read identity only behind Builder's required Entra authentication boundary."""

import base64
import binascii
import json

from starlette.requests import Request

from config import Settings, guid


PRINCIPAL_HEADER = "x-ms-client-principal-id"
TENANT_CLAIM = "http://schemas.microsoft.com/identity/claims/tenantid"
OBJECT_CLAIM = "http://schemas.microsoft.com/identity/claims/objectidentifier"


def verified_identity(request: Request, settings: Settings) -> str | None:
    values = {}
    for name in (PRINCIPAL_HEADER, "x-ms-client-principal-idp", "x-ms-client-principal"):
        headers = request.headers.getlist(name)
        if len(headers) != 1 or len(headers[0]) > 16_384:
            return None
        values[name] = headers[0]
    if values["x-ms-client-principal-idp"] != "aad":
        return None
    try:
        principal_id = guid(values[PRINCIPAL_HEADER])
        payload = json.loads(base64.b64decode(values["x-ms-client-principal"], validate=True))
        if not isinstance(payload, dict) or payload.get("auth_typ") != "aad":
            return None
        claims = payload.get("claims")
        if not isinstance(claims, list) or not all(isinstance(item, dict) for item in claims):
            return None
        tenants = [item.get("val") for item in claims if item.get("typ") == TENANT_CLAIM]
        objects = [item.get("val") for item in claims if item.get("typ") == OBJECT_CLAIM]
        if tenants != [settings.tenant_id] or objects != [principal_id]:
            return None
        return principal_id
    except (ValueError, TypeError, binascii.Error, UnicodeError, RecursionError):
        return None
