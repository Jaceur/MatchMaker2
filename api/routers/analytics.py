"""Admin analytics board: approval drivers, score calibration, coverage."""
from fastapi import APIRouter, Depends

from ..analytics import compute_analytics
from ..security import require_admin

router = APIRouter(prefix="/analytics", tags=["analytics"], dependencies=[Depends(require_admin)])


@router.get("")
def analytics() -> dict:
    return compute_analytics()
