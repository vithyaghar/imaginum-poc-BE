from fastapi import APIRouter
from controllers.drive_controller import search_drive_files_controller

router = APIRouter(prefix="/drive", tags=["Drive"])


@router.get("/search")
async def search_drive_files(session_id: str, query: str = ""):
    """
    Search Google Drive files by name for the @ mention dropdown.

    Returns { connected: false } when Drive is not connected for this session.
    Empty query returns 10 most recently modified supported files.
    """
    return await search_drive_files_controller(session_id, query)
