"""Auth endpoints: log in for a JWT, and read back the current user."""
from fastapi import APIRouter, Depends, HTTPException, status

from ..schemas import LoginRequest, TokenResponse, UserOut
from ..security import authenticate, create_access_token, get_current_user, CurrentUser

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest):
    user = authenticate(body.username, body.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )
    token = create_access_token(user.username, user.role)
    return TokenResponse(
        access_token=token,
        user=UserOut(username=user.username, role=user.role),
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser = Depends(get_current_user)):
    return UserOut(username=user.username, role=user.role)
