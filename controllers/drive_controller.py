from services.google_drive_service import search_files
from helper.response_handler import response_handler


async def search_drive_files_controller(session_id: str, query: str):
    try:
        result = search_files(session_id, query)
        return response_handler.success(
            message="Files fetched successfully",
            status_code=200,
            data=result,
        )
    except Exception as error:
        print(f"Drive search error: {error}")
        return response_handler.error(
            message="Failed to search Drive files",
            status_code=500,
        )
