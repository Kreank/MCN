from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User


@admin.register(User)
class McnUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("MCN", {"fields": ("app_user_id",)}),)
    list_display = ("username", "email", "app_user_id", "is_staff")
