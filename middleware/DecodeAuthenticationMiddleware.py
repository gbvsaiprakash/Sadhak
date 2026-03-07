# myapp/middleware.py
from django.http import JsonResponse
import json
import base64

from django.contrib.auth import authenticate
from rest_framework.authentication import TokenAuthentication
from rest_framework_simplejwt.tokens import AccessToken
from django.http.multipartparser import MultiPartParser
from django.utils.datastructures import MultiValueDictKeyError

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


class DebugAuthenticationMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        custom_auth_token = request.headers.get("CustomAuthToken")

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

            meta_data = {
                "created_by": user.user_id,
                "updated_by": user.user_id,
                "updated_by_name": user.first_name,
                "device_id": "device_id",
                "device_type": "device_type",
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

                    data["logged_user"] = user.username
                    data["meta_data"] = meta_data

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
        return response