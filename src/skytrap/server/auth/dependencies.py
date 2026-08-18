from fastapi import HTTPException, Request

from skytrap.server.auth.jwt import verify_access_token

ACCESS_COOKIE = "skytrap_access"
REFRESH_COOKIE = "skytrap_refresh"


def get_current_user_id(request: Request) -> int:
    token = request.cookies.get(ACCESS_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    user_id = verify_access_token(token, request.app.state.settings.secret_key)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    return user_id


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
