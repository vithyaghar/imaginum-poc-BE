#!/usr/bin/env python
# -- coding:utf-8 --
###
# Filename: controllers/canva_controller.py
# Author: Vithyaghar M
#
# Copyright (c) 2026 Trinom Digital
###

from services.canva_service import search_designs
from helper.response_handler import response_handler


async def search_canva_designs_controller(session_id: str, query: str):
    try:
        result = search_designs(session_id, query)
        return response_handler.success(
            message="Designs fetched successfully",
            status_code=200,
            data=result,
        )
    except Exception as error:
        print(f"Canva search error: {error}")
        return response_handler.error(
            message="Failed to search Canva designs",
            status_code=500,
        )
