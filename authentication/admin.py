from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import User, LoginAttempt, OTPVerifyAttempt, CardDetails, PaymentMethod


class UserAdmin(BaseUserAdmin):

    # The fields to be used in displaying the User model.
    # These override the definitions on the base UserAdmin
    # that reference specific fields on auth.User.
    list_display = ["email", "username", "is_admin","is_active", "is_staff_member", "id"]
    list_filter = ["is_admin"]
    fieldsets = [
        (None, {"fields": ["email", "password"]}),
        ("Personal info", {"fields": ["username","verification_token","verification_token_expiry","otp","otp_expiry","is_seller","user_profile_image"]}),
        ("Permissions", {"fields": ["is_admin","is_staff_member","is_two_factor_authentication_enabled","two_factor_pin"]}),
    ]
    # add_fieldsets is not a standard ModelAdmin attribute. UserAdmin
    # overrides get_fieldsets to use this attribute when creating a user.
    add_fieldsets = [
        (
            None,
            {
                "classes": ["wide"],
                "fields": ["email", "username", "password1", "password2"],
            },
        ),
    ]
    search_fields = ["email"]
    ordering = ["email"]
    filter_horizontal = []




# Now register the new UserAdmin...
admin.site.register(User, UserAdmin)
admin.site.register(LoginAttempt)
admin.site.register(OTPVerifyAttempt)
admin.site.register(CardDetails)
admin.site.register(PaymentMethod)