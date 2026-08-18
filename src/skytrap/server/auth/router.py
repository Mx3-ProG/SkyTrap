from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from skytrap.server.auth.dependencies import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    client_ip,
    get_current_user_id,
)
from skytrap.server.auth.jwt import ACCESS_TOKEN_TTL_SECONDS, sign_access_token
from skytrap.server.auth.store import REFRESH_TOKEN_TTL_DAYS, AuthStore

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_TOKEN_TTL_SECONDS = REFRESH_TOKEN_TTL_DAYS * 86400


class LoginRequest(BaseModel):
    email: str
    password: str


class OtpVerifyRequest(BaseModel):
    email: str
    code: str


class OtpResendRequest(BaseModel):
    email: str


def _set_session_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        ACCESS_COOKIE,
        access_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=ACCESS_TOKEN_TTL_SECONDS,
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=REFRESH_TOKEN_TTL_SECONDS,
    )


@router.post("/login")
def login(payload: LoginRequest, request: Request) -> dict:
    store: AuthStore = request.app.state.auth_store
    ip = client_ip(request)

    if store.is_rate_limited(ip):
        raise HTTPException(status_code=429, detail="Too many attempts, try again later")

    user = store.get_user_by_email(payload.email)
    success = user is not None and store.verify_password(user, payload.password)
    store.record_login_attempt(ip, payload.email, "password", success)

    if not success:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    code = store.create_otp(user.id)
    if code is None:
        raise HTTPException(status_code=429, detail="Too many codes requested, try again soon")

    request.app.state.email_sender.send(
        user.email, "Your SkyTrap login code", f"Your login code is: {code}"
    )
    return {"status": "otp_sent"}


@router.post("/otp/resend")
def resend_otp(payload: OtpResendRequest, request: Request) -> dict:
    store: AuthStore = request.app.state.auth_store
    user = store.get_user_by_email(payload.email)
    if user is None:
        # Same response either way (don't reveal whether the email exists) — the
        # 429/200 distinction below is about the OTP issuance policy, not user
        # enumeration, so an unknown email is folded into the generic "ok" path.
        return {"status": "otp_sent"}

    code = store.create_otp(user.id)
    if code is None:
        raise HTTPException(status_code=429, detail="Please wait before requesting another code")

    request.app.state.email_sender.send(
        user.email, "Your SkyTrap login code", f"Your login code is: {code}"
    )
    return {"status": "otp_sent"}


@router.post("/otp/verify")
def verify_otp(payload: OtpVerifyRequest, request: Request, response: Response) -> dict:
    store: AuthStore = request.app.state.auth_store
    ip = client_ip(request)

    user = store.get_user_by_email(payload.email)
    success = user is not None and store.verify_otp(user.id, payload.code)
    store.record_login_attempt(ip, payload.email, "otp", success)

    if not success:
        raise HTTPException(status_code=401, detail="Invalid or expired code")

    access_token = sign_access_token(user.id, request.app.state.settings.secret_key)
    refresh_token = store.create_refresh_token(user.id)
    _set_session_cookies(response, access_token, refresh_token)
    return {"status": "ok"}


@router.post("/refresh")
def refresh(request: Request, response: Response) -> dict:
    store: AuthStore = request.app.state.auth_store
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=401, detail="No refresh token")

    result = store.rotate_refresh_token(raw_token)
    if result is None:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user_id, new_refresh_token = result
    access_token = sign_access_token(user_id, request.app.state.settings.secret_key)
    _set_session_cookies(response, access_token, new_refresh_token)
    return {"status": "ok"}


@router.post("/logout")
def logout(request: Request, response: Response) -> dict:
    store: AuthStore = request.app.state.auth_store
    raw_token = request.cookies.get(REFRESH_COOKIE)
    if raw_token:
        store.revoke_refresh_token(raw_token)
    response.delete_cookie(ACCESS_COOKIE)
    response.delete_cookie(REFRESH_COOKIE)
    return {"status": "ok"}


@router.get("/me")
def me(request: Request, user_id: int = Depends(get_current_user_id)) -> dict:
    store: AuthStore = request.app.state.auth_store
    user = store.get_user(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return {"email": user.email}
