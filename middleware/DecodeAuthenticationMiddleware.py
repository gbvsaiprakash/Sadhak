# myapp/middleware.py
from django.http import JsonResponse
import json
import base64

from django.contrib.auth import authenticate
from rest_framework.authentication import TokenAuthentication
from rest_framework_simplejwt.tokens import AccessToken
from django.http.multipartparser import MultiPartParser
from django.utils.datastructures import MultiValueDictKeyError
from user_management.models import AuditLog

from user_management.models import User
# from utils import set_current_user


def get_additional_meta_data(request):
    try:
        additional_meta_data = request.headers.get("user_metadata", {})
        if isinstance(additional_meta_data, str):
            additional_meta_data = json.loads(additional_meta_data)
        return additional_meta_data
    except Exception as e:
        print("No Additional Meta Data Found", e)
        return {}

def get_device_data(request):
    data = {}
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    data["device_type"] = request.META.get("HTTP_USER_AGENT", "")

    if x_forwarded_for:
        data['device_id'] = x_forwarded_for.split(",")[0]
    else:
        data['device_id'] = request.META.get("REMOTE_ADDR")

    return data

class DebugAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        custom_auth_token = request.headers.get("CustomAuthToken")
        log_data = {"action":request.resolver_match,"method":request.method,"path":request.path,"request_data":None,"response_data":None,"status":None}
        if request.get_full_path() not in ["api/user/login/", "/api/user/token/refresh/"]:
            log_data["request_data"] = json.loads(request.body)

        if custom_auth_token:
            request.META["HTTP_AUTHORIZATION"] = f"Bearer {custom_auth_token}"
            auth_header = f"Bearer {custom_auth_token}"
        else:
            auth_header = request.headers.get("Authorization", "")

        user = None

        # BASIC AUTH
        if auth_header.startswith("Basic "):
            _, encoded_credentials = auth_header.split(" ", 1)
            decoded = base64.b64decode(encoded_credentials).decode("utf-8")
            username, password = decoded.split(":", 1)
            user = authenticate(request, username=username, password=password)

        # DRF TOKEN AUTH
        elif auth_header.startswith("Token "):
            token = auth_header.split(" ")[1]
            try:
                user, _ = TokenAuthentication().authenticate_credentials(
                    token.encode("utf-8")
                )
            except Exception:
                user = None

        # JWT BEARER AUTH
        elif auth_header.startswith("Bearer "):
            try:
                token = auth_header.split(" ")[1]
                token = AccessToken(token)
                user_id = token.payload["user_id"]
                user = User.objects.get(user_id=user_id)
            except Exception:
                user = None

        if user and request.get_full_path() not in ["api/user/login/", "/api/user/token/refresh/"]:

            additional_meta_data = get_additional_meta_data(request)
            device_data = get_device_data(request)

            meta_data = {
                "created_by": user.user_id,
                "updated_by": user.user_id,
                "updated_by_name": user.first_name,
                "device_id": device_data.get("device_id"),
                "device_type": device_data.get("device_type"),
                **additional_meta_data,
            }
            # set_current_user(
            #     logged_user=user.username,
            #     meta_data=additional_meta_data,
            # )

            request.user = user

            # JSON REQUEST
            if request.method in ["POST", "PUT", "PATCH"] and request.content_type == "application/json":
                try:
                    body_data = request.body or json.dumps({})
                    data = json.loads(body_data)

                    data["logged_user"] = log_data["user"] = user.username
                    data["meta_data"] = log_data["meta_data"] = meta_data

                    request._body = json.dumps(data, default=str).encode("utf-8")

                except json.JSONDecodeError:
                    print("Error decoding JSON body")

            # FORM REQUEST
            elif (
                request.method == "POST"
                and request.content_type == "application/x-www-form-urlencoded"
            ):

                data = request.POST.copy()

                data["logged_user"] = user.username
                data["meta_data"] = meta_data

                request.POST = data

            # MULTIPART REQUEST
            elif request.content_type and request.content_type.startswith(
                "multipart/form-data"
            ):
                try:
                    parser = MultiPartParser(request.META, request, request.upload_handlers)
                    data, files = parser.parse()

                    form_data = data.copy()
                    form_data["logged_user"] = user.username
                    form_data["meta_data"] = meta_data

                    request.POST = form_data
                    request._files = files
                    request.user = user

                except MultiValueDictKeyError as e:
                    print(f"Error parsing multipart data: {e}")

        response = self.get_response(request)
        log_data["action"] = request.resolver_match.view_name
        log_data["status"] = response.status_code
        if request.get_full_path() in ["api/user/login/", "/api/user/token/refresh/"]:
            log_data["request_data"] = None
        if log_data["status"] >= 400:
            log_data["response_data"] = json.dumps(response.data,default=str)
        log = AuditLog.objects.create(
            user=user,
            action=log_data.get("action",""),
            endpoint=log_data.get("path"),
            ip_address=log_data.get("meta_data",{}).get("device_id"),
            device_type=log_data.get("meta_data",{}).get("device_type"),
            request_data=log_data.get("request_data"),
            response_data=log_data.get("response_data"),
            status_code=log_data.get("status")
            )
        return response