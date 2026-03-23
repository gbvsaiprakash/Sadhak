from rest_framework import status
from rest_framework.exceptions import APIException


class TrackerAPIException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "TRACKER_ERROR"
    default_detail = "An unknown tracker error occurred."

    def __init__(self, code=None, message=None, details=None, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        payload = {
            "error": True,
            "code": code or self.default_code,
            "message": message or self.default_detail,
            "details": details or {},
        }
        super().__init__(detail=payload)


def raise_tracker_error(code, message, details=None, status_code=None):
    raise TrackerAPIException(code=code, message=message, details=details, status_code=status_code)
