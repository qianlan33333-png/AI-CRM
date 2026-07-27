from __future__ import annotations

from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from aicrm_next.platform.shared.runtime import production_environment
from aicrm_next.platform.shared.runtime_settings import runtime_csv, runtime_setting

from .client_authentication import ClientAuthenticationError, client_credentials, request_source_ip
from .credentials import CredentialHasher
from .repository import PostgresAuthRepository
from .service import ApiClientService, AuthError, AuthServiceConfig
from .sessions import AuthSessionService
from .webhook_hmac import WebhookHmacVerifier


router = APIRouter()


def auth_client_service(request: Request) -> ApiClientService:
    configured = getattr(request.app.state, "auth_client_service", None)
    if isinstance(configured, ApiClientService):
        return configured
    issuer = runtime_setting("AICRM_AUTH_ISSUER").rstrip("/")
    signing_key = runtime_setting("AICRM_AUTH_JWT_SIGNING_KEY")
    service = ApiClientService(
        PostgresAuthRepository(),
        AuthServiceConfig(issuer=issuer, signing_key=signing_key),
    )
    request.app.state.auth_client_service = service
    return service


def auth_session_service(request: Request) -> AuthSessionService:
    configured = getattr(request.app.state, "auth_session_service", None)
    if isinstance(configured, AuthSessionService):
        return configured
    pepper = runtime_setting("AICRM_AUTH_SESSION_HASH_PEPPER")
    service = AuthSessionService(PostgresAuthRepository(), CredentialHasher(pepper))
    request.app.state.auth_session_service = service
    return service


def auth_webhook_verifier(request: Request) -> WebhookHmacVerifier:
    configured = getattr(request.app.state, "auth_webhook_verifier", None)
    if isinstance(configured, WebhookHmacVerifier):
        return configured
    verifier = WebhookHmacVerifier(PostgresAuthRepository())
    request.app.state.auth_webhook_verifier = verifier
    return verifier


@router.post("/oauth/token", name="oauth_token")
async def token_endpoint(request: Request) -> JSONResponse:
    if production_environment() and not _request_is_https(request):
        return _error("tls_required", status_code=400)
    form = _form_values(await request.body())
    try:
        if str(form.get("grant_type") or "") != "client_credentials":
            raise AuthError("unsupported_grant_type", status_code=400)
        client_id, secret = client_credentials(headers=request.headers, form=form)
        scopes = tuple(str(form.get("scope") or "").split())
        source_ip = request_source_ip(
            peer_ip=str(request.client.host if request.client else ""),
            headers=request.headers,
            trusted_proxy_cidrs=tuple(sorted(runtime_csv("AICRM_AUTH_TRUSTED_PROXY_ADDRESSES"))),
        )
        issued = auth_client_service(request).issue_client_credentials_token(
            client_id=client_id,
            client_secret=secret,
            audience=str(form.get("audience") or ""),
            requested_scopes=scopes,
            source_ip=source_ip,
        )
    except ClientAuthenticationError as exc:
        return _error(exc.error, status_code=exc.status_code)
    except AuthError as exc:
        return _error(exc.error, status_code=exc.status_code)
    except (TypeError, ValueError):
        return _error("invalid_request", status_code=400)
    return JSONResponse(
        {
            "access_token": issued.access_token,
            "token_type": issued.token_type,
            "expires_in": issued.expires_in,
            "scope": issued.scope,
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def request_id(request: Request) -> str:
    return str(request.headers.get("X-Request-Id") or f"req_{uuid4().hex}").strip()[:128]


def request_ip(request: Request) -> str:
    return request_source_ip(
        peer_ip=str(request.client.host if request.client else ""),
        headers=request.headers,
        trusted_proxy_cidrs=tuple(sorted(runtime_csv("AICRM_AUTH_TRUSTED_PROXY_ADDRESSES"))),
    )


def _form_values(body: bytes) -> dict[str, str]:
    parsed = parse_qs(bytes(body or b"").decode("utf-8"), keep_blank_values=True)
    return {key: str(values[-1] if values else "") for key, values in parsed.items()}


def _request_is_https(request: Request) -> bool:
    forwarded = str(request.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip().lower()
    return str(request.url.scheme).lower() == "https" or forwarded == "https"


def _error(error: str, *, status_code: int) -> JSONResponse:
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache"}
    if status_code == 401:
        headers["WWW-Authenticate"] = 'Basic realm="aicrm-client-credentials"'
    return JSONResponse({"ok": False, "error": error}, status_code=status_code, headers=headers)
