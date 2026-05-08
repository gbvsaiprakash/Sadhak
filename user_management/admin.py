from django.contrib import admin

# Register your models here.
from django.contrib.auth.forms import AuthenticationForm
AuthenticationForm.base_fields['username'].max_length = 36
AuthenticationForm.base_fields['username'].widget.attrs['maxlength'] = 36