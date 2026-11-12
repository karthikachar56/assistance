"""User authentication endpoints (Sign Up, Login, Session Profile, Logout)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException

from app.db.crypto import hash_password, verify_password
from app.deps import AppContainer, get_container
from app.schemas.models import AuthResponse, UserLoginRequest, UserOut, UserSignUpRequest


router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_to_out(row: dict, container: AppContainer) -> UserOut:
    return UserOut(
        id=int(row["id"]),
        name=row["name"],
        shop_name=row.get("shop_name") or "",
        email=row["email"],
        phone=container.cipher.decrypt(row.get("phone_enc")),
        preferred_language=row.get("preferred_language") or "hi-IN",
        created_at=row["created_at"],
        last_login_at=row.get("last_login_at"),
    )


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def resolve_user_from_token(token: str | None, container: AppContainer) -> dict:
    """Validate a signed Bearer token and return the matching user record."""
    claims = container.cipher.verify_token(token)
    if not claims or claims.get("sub") is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        user_id = int(claims["sub"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="Invalid session token.") from exc
    user = container.db.get_user_by_id(user_id)
    if not user:
        if container.storage_fallback_reason is not None and container.settings.has_mongo:
            raise HTTPException(
                status_code=503,
                detail="Primary database temporarily unreachable; please retry.",
            )
        raise HTTPException(status_code=401, detail="User session not found. Please sign in again.")
    return user


def get_current_user(
    authorization: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> dict:
    token = _extract_bearer_token(authorization)
    return resolve_user_from_token(token, container)


def get_scoped_container(
    authorization: str | None = Header(default=None),
    container: AppContainer = Depends(get_container),
) -> AppContainer:
    """Resolve an authenticated account's dedicated database container, or the default container when anonymous."""
    if authorization is None or not authorization.strip():
        return container
    token = _extract_bearer_token(authorization)
    user = resolve_user_from_token(token, container)
    return container.for_user(user)


@router.post("/signup", response_model=AuthResponse, status_code=201)
def signup_user(req: UserSignUpRequest, container: AppContainer = Depends(get_container)) -> AuthResponse:
    existing = container.db.get_user_by_email(req.email)
    if existing:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    now_iso = datetime.now(timezone.utc).isoformat()
    clean_name = req.name.strip()
    clean_shop = (req.shop_name or "").strip() or f"{clean_name}'s Khata"
    phone_enc = container.cipher.encrypt(req.phone.strip()) if req.phone and req.phone.strip() else None
    pw_hash = hash_password(req.password)

    try:
        created = container.db.create_user(
            name=clean_name,
            shop_name=clean_shop,
            email=req.email,
            phone_enc=phone_enc,
            password_hash=pw_hash,
            preferred_language=req.preferred_language,
            created_at=now_iso,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    container.db.update_user_last_login(int(created["id"]), now_iso)
    created["last_login_at"] = now_iso
    container.for_user(created)
    token = container.cipher.sign_token({"sub": int(created["id"]), "email": created["email"]})
    return AuthResponse(access_token=token, user=_user_to_out(created, container))


@router.post("/login", response_model=AuthResponse)
def login_user(req: UserLoginRequest, container: AppContainer = Depends(get_container)) -> AuthResponse:
    user = container.db.get_user_by_email(req.email)
    if not user or not verify_password(req.password, user.get("password_hash") or ""):
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    now_iso = datetime.now(timezone.utc).isoformat()
    container.db.update_user_last_login(int(user["id"]), now_iso)
    user["last_login_at"] = now_iso
    container.for_user(user)
    token = container.cipher.sign_token({"sub": int(user["id"]), "email": user["email"]})
    return AuthResponse(access_token=token, user=_user_to_out(user, container))


@router.get("/me", response_model=UserOut)
def get_authenticated_user(
    user: dict = Depends(get_current_user),
    container: AppContainer = Depends(get_container),
) -> UserOut:
    return _user_to_out(user, container)


@router.post("/logout")
def logout_user() -> dict:
    return {"status": "logged_out"}
