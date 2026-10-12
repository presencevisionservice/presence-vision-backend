from django.urls import path
from . import views


urlpatterns = [

    # ---------------- NORMAL USER ENDPOINTS ------------------------

    path('check_user_authentication', views.CheckUserAuthentication.as_view()),
    
    path('get_user_details', views.GetUserDetails.as_view()),

    path('user_registration', views.UserRegistrationView.as_view()),

    path('user_otp_verify', views.VerifyUserOTP.as_view()),

    path('user_login', views.UserLoginView.as_view()),

    path('user_logout', views.UserLogoutView.as_view()),

    path('login/google/', views.GoogleRegistrationRedirect.as_view(), name='google_login'),

    path('callback/google/', views.GoogleCallbackView.as_view(), name='google_callback'),
    
    path('get_authorization_tokens/<str:verification_token>', views.GetAuthorizationTokensAfterGoogleLogin.as_view()),

    path('refresh_user_tokens', views.RefreshUserTokens.as_view()),
    
    path('reset_user_password', views.PasswordResetView.as_view()),
    
    path('verify_reset_password_otp', views.VerifyResetPasswordOTP.as_view()),

    path('change_user_password', views.ChangeUserPassword.as_view()),
    
    path('set_two_step_pin', views.SetTwoFactorAuthenticationPIN.as_view()),

    path('verify_two_step_pin', views.TwoFactorPINVerify.as_view()),
 

]

