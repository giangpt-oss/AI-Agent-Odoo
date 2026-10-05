from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import RedirectResponse
from app.connectors.google.client import GoogleOAuthService

router = APIRouter(prefix="/oauth/google", tags=["Google OAuth"])
oauth_service = GoogleOAuthService()


@router.get("/authorize")
async def google_authorize(state: str = Query(..., description="Employee ID hoặc Telegram Chat ID")):
    """Sinh URL điều hướng nhân viên tới màn hình xác thực Google OAuth."""
    auth_url = oauth_service.get_authorization_url(state=state)
    return {"authorization_url": auth_url}


@router.get("/callback")
async def google_callback(
    code: str = Query(..., description="Authorization code từ Google"),
    state: str = Query(..., description="Employee ID đã gửi đi"),
):
    """Tiếp nhận Authorization Code từ Google và đổi lấy Access/Refresh Tokens."""
    try:
        tokens = await oauth_service.exchange_code_for_tokens(code)
        # Refresh token sẽ được mã hóa với token_cipher và lưu vào database ở services/employee.py
        return {
            "status": "success",
            "message": "Liên kết tài khoản Google thành công!",
            "employee_id": state,
            "has_refresh_token": "refresh_token" in tokens,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi trao đổi token Google: {str(e)}")
