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
#  File: \business_commnunication\controllers\thread_controller.py
#  Project: ps1
#  Created Date: Monday, March 9th 2026, 11:33:56 am
#  Author: Naveena J <naveena@codestax.ai>
#  -----
#  Last Modified: 
#  Modified By: 
#  -----
#  HISTORY:
#  Date         By  Comments
#  ---------------------------------------------------------------------------
###
from services.thread_service import ThreadService
from helper.response_handler import ResponseHandler

response_handler = ResponseHandler()


class ThreadController:

    def __init__(self):
        self.thread_service = ThreadService()

    async def get_all_threads_controller(self):
        try:

            threads = self.thread_service.get_all_threads()

            if not threads:
                return response_handler.success(
                    message="No threads found",
                    status_code=200,
                    data=[]
                )

            return response_handler.success(
                message="Threads fetched successfully",
                status_code=200,
                data=threads
            )

        except Exception as error:
            print(f"Error: {error}")

            return response_handler.error(
                message="Internal server error",
                status_code=500
            )

    async def get_thread_by_id_controller(self, thread_id: str):

        try:

            thread = self.thread_service.get_thread_by_id(thread_id)

            if not thread:
                return response_handler.error(
                    message="Thread not found",
                    status_code=404
                )

            return response_handler.success(
                message="Thread fetched successfully",
                status_code=200,
                data=thread
            )

        except Exception as error:

            print(f"Error: {error}")

            return response_handler.error(
                message="Internal server error",
                status_code=500
            )

    async def update_thread_title_controller(self, thread_id: str, business_name: str):
        try:
            updated = self.thread_service.update_thread_title(thread_id, business_name)
            if not updated:
                return response_handler.error(
                    message="Thread not found",
                    status_code=404
                )
            return response_handler.success(
                message="Title updated successfully",
                status_code=200
            )
        except Exception as error:
            print(f"Error: {error}")
            return response_handler.error(
                message="Internal server error",
                status_code=500
            )

thread_controller = ThreadController()