#!/usr/bin/env python
# -- coding:utf-8 --
###
#  Trinom Digital Pvt Ltd ("COMPANY") CONFIDENTIAL
#  Copyright (c) 2026 Trinom Digital Pvt Ltd, All rights reserved
# 
#  NOTICE: All information contained herein is, and remains the property
#  of COMPANY. The intellectual and technical concepts contained herein are
#  proprietary to COMPANY and may be protected by law.
# 
#  File: \business_commnunication\helper\response_handler.py
#  Project: ps1
#  Created Date: Monday, March 9th 2026, 11:43:35 am
#  Author: Naveena J <naveena@codestax.ai>
#  -----
#  Last Modified: 
#  Modified By: 
#  -----
#  HISTORY:
#  Date         By  Comments
#  ---------------------------------------------------------------------------
###
#!/usr/bin/env python
# -*- coding:utf-8 -*-
###
# Filename: /Users/manickam/Documents/commsAI_repos/COMMS_AI_BE_PY/app/helper/responses_handler.py
# Path: /Users/manickam/Documents
# Created Date: Wednesday, September 10th 2025, 4:22:01 pm
# Author: Manickam Venkatachalam
# 
# Copyright (c) 2025 Trinom Digital Pvt Ltd
###
import logging
from typing import Any, Optional
from fastapi import HTTPException, Response
from fastapi.responses import JSONResponse

class ResponseHandler:
    """
    Response handler class to maintain consistent API response structure
    """
    
    def success(self, message: str, status_code: int, data: Optional[Any] = None, ) -> JSONResponse:
        """
        Success response handler - always returns 200
        """
        if status_code == 204:
            # 204 No Content must not have a body
            return Response(status_code=204)
        
        response_data = {
            "success": True,
            "message": message
        }
        if data is not None:
            response_data["data"] = data
        
        return JSONResponse(
            status_code = status_code,
            content = response_data
        )
    
    def error(self, message: str, status_code: int) -> JSONResponse:
        """
        Error response handler - always returns 500
        """
        logging.error(f"[ERROR] API FAILED: {message}")
        
        raise HTTPException(
           status_code = status_code,
           detail={
               "success": False,
               "message": message
           }
        )
    
response_handler = ResponseHandler()