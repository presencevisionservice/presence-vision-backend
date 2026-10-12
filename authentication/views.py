# CORE PYTHON DEPENDENCIES
from threading import Thread
import time
import urllib.parse

import base64

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from django.conf import settings
from django.template.loader import render_to_string

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from premailer import transform

# REST FRAMEWORK DEPENDENCIES
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination


# SIMPLE JWT DEPENDENCIES
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken, UntypedToken
from rest_framework_simplejwt.exceptions import TokenError, InvalidToken, AuthenticationFailed


# SERIALIZERS
from .serializers import UserRegistrationSerializer


# MODELS
from authentication.models import User, LoginAttempt, OTPVerifyAttempt, TwoStepVerificationAttempt


# DJANGO DEPENDENCIES
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect
from django.http import HttpResponseRedirect


# SLIGHTLY USED THIRD PARTY DEPENDENCIES
from premailer import transform
import secrets, random, string, requests
from datetime import timedelta, datetime, timezone as dt_timezone


# ------------------------------- NORMAL USER AUTHENTICATION ---------------------------------------


class CheckUserAuthentication(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request):

        return Response("Authenticated",status=status.HTTP_200_OK)



class GetUserDetails(APIView):

    permission_classes = [IsAuthenticated]

    def get(self, request):

        return Response({"username" : request.user.username, "email" : request.user.email, "is_seller" : request.user.is_seller, "is_manager" : request.user.is_staff_member ,"is_admin" : request.user.is_admin, "is_protected" : request.user.is_two_factor_authentication_enabled})




class UserRegistrationView(APIView):

    def post(self, request):

        serializer = UserRegistrationSerializer(data=request.data)

        if serializer.is_valid():

            user = serializer.save()

            assign_otp(user=user)

            assign_verification_token(user=user)

            email_thread = Thread(target=send_stylized_email,args=(user.email, "Verify Your Account", "otp.html", {'username': user.username, 'otp':user.otp}))

            email_thread.start()

            return Response({'verification_token':user.verification_token},status=status.HTTP_201_CREATED)
        
    
        else:

            first_error_field = next(iter(serializer.errors))  # Get the first key from the errors dictionary

            first_error_message = serializer.errors[first_error_field][0]  # Get the first error message for that field

            return Response({'error':first_error_message},status=status.HTTP_400_BAD_REQUEST)



class GoogleRegistrationRedirect(APIView):

    def get(self, request, *arga, **kwargs):

        GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
        CLIENT_ID = settings.GOOGLE_OAUTH_CLIENT_ID
        REDIRECT_URI = f"{settings.APPLICATION_URL}/authentication/callback/google"
        SCOPE = "https://www.googleapis.com/auth/userinfo.email https://www.googleapis.com/auth/userinfo.profile"

        print("GOOGLE REDIRECT URI:", REDIRECT_URI)
        
        # Construct The Google Auth URL

        params = {
            "client_id" : CLIENT_ID,
            "redirect_uri" : REDIRECT_URI,
            "response_type" : "code",
            "scope" : SCOPE,
            "access_type" : "offline",
            "prompt" : "consent",
        }

        url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"

        return redirect(url)
    


class GoogleCallbackView(APIView):

    def get(self, request, *args, **kwargs):

        code = request.GET.get('code')

        if not code:
            raise ValidationError({
                "error": "Authorization code not provided"
            })

        token_url = "https://oauth2.googleapis.com/token"

        data = {
            "code": code,
            "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri": f"{settings.APPLICATION_URL}/authentication/callback/google",
            "grant_type": "authorization_code",
        }

        token_response = requests.post(token_url, data=data)
        token_data = token_response.json()

        if "access_token" not in token_data:
            raise ValidationError({
                "error": "Failed to retrieve access token",
                "details": token_data
            })

        access_token = token_data["access_token"]

        user_info_url = "https://www.googleapis.com/oauth2/v1/userinfo"

        headers = {
            "Authorization": f"Bearer {access_token}"
        }

        user_info_response = requests.get(
            user_info_url,
            headers=headers
        )

        user_info = user_info_response.json()

        email = user_info.get("email")
        name = user_info.get("name")

        if not email:
            raise ValidationError({
                "error": "Failed to fetch user information from Google"
            })

        # Check whether user already exists
        user = User.objects.filter(email=email).first()

        if user is None:
            # Registration
            user = User.objects.create(
                email=email,
                username=name
            )

            user.set_password(make_user_password())
            user.is_active = True
            user.save()

        # Login for existing user OR newly registered user
        verification_to_pass = assign_verification_token(user=user)

        return redirect(
            f"{settings.FRONTEND_URL}/auth/google-registration-callback"
            f"?verification_token={verification_to_pass}"
        )


class GetAuthorizationTokensAfterGoogleLogin(APIView):

    def get(self, request, verification_token):

        try:
            user = User.objects.get(
                verification_token=verification_token
            )

            tokens = generate_user_tokens(user=user)

            response = Response(
                {
                    "msg": "All Set",
                    "store_date": datetime.now().strftime("%Y-%m-%d"),
                    "store_time": datetime.now().strftime("%H:%M:%S")
                },
                status=status.HTTP_200_OK
            )

            email_thread = Thread(
                target=send_stylized_email,
                args=(
                    user.email,
                    "New Login Detected",
                    "account_login_notify.html",
                    {
                        'username': user.username,
                        'login_time': datetime.now().strftime("%B %d, %Y at %I:%M %p")
                    }
                )
            )

            email_thread.start()            

            return set_tokens_and_expiry(
                response_object=response,
                tokens=tokens
            )

        except User.DoesNotExist:
            return Response(
                "Verification Token Isn't Valid",
                status=status.HTTP_400_BAD_REQUEST
            )

class VerifyUserOTP(APIView):


    def track_verify_attempt(self, request, user=None, success=False):

        ip_address = get_client_ip(request=request)

        # Delete the Succeccful LoginAttempt of the IP Adress if ever before

        OTPVerifyAttempt.objects.filter(ip_address=ip_address, success=True).delete()
        
        # Create a new Failed LoginAttempt of the IP Address

        OTPVerifyAttempt.objects.create(user=user, ip_address=ip_address, success=success)

    
    def delete_attempts_before_threshold(self, ip_address, duration_minutes):

        time_threshold = timezone.now() - timedelta(minutes=duration_minutes)

        # Delete the Older Failed Login Attempts becuase they are no more revelant

        OTPVerifyAttempt.objects.filter(ip_address=ip_address, success=False, timestamp__lte=time_threshold).delete()

    
    def too_many_attempts(self, request, duration_minutes=15, max_attempts=5):
        
        ip_address = get_client_ip(request)

        time_threshold = timezone.now() - timedelta(minutes=duration_minutes)

        delete_old_attempts_thred = Thread(target=self.delete_attempts_before_threshold,args=(ip_address,duration_minutes))

        delete_old_attempts_thred.start()

        # Count failed login attempts for the IP in the given duration
        failed_attempts = OTPVerifyAttempt.objects.filter(ip_address=ip_address, success=False, timestamp__gte=time_threshold).count()

        return failed_attempts >= max_attempts


    def post(self, request):

        if self.too_many_attempts(request=request):

            return Response({"error":"Too many Verification Requests. Try Again Later"},status=status.HTTP_429_TOO_MANY_REQUESTS)

        incoming_verification_token = request.data.get("verification_token",None)
        incoming_otp = request.data.get("otp",None)

        if incoming_verification_token is not None:

            try:

                user = User.objects.get(verification_token=incoming_verification_token)

                if user.verification_token_expiry is not None and user.verification_token_expiry > timezone.now():

                    if user.otp_expiry is not None and user.otp_expiry > timezone.now() and user.otp == incoming_otp:

                        self.track_verify_attempt(request=request, user=user, success=True)

                        user.is_active = True
                        user.otp = None
                        user.otp_expiry = None
                        user.verification_token = None
                        user.verification_token_expiry = None
            
                        clear_failed_attempts_thread = Thread(target=clean_failed_attempts,args=(request, "otp-attempt"))

                        clear_failed_attempts_thread.start()

                        user.save()

                        tokens = generate_user_tokens(user=user)

                        response = Response({"msg":"OTP Verified, User is Now Active", "store_date" : datetime.now().strftime("%Y-%m-%d"), "store_time" : str(datetime.now().strftime("%H:%M:%S"))},status=status.HTTP_200_OK)

                        return set_tokens_and_expiry(response_object=response, tokens=tokens)
                    
                    else:

                        self.track_verify_attempt(request=request, success=False)

                        return Response({"error":"Invalid or Expired OTP"},status=status.HTTP_400_BAD_REQUEST)
                    
                else:

                    self.track_verify_attempt(request=request, success=False)

                    return Response({"error":"Invalid or Expired Token"}, status=status.HTTP_400_BAD_REQUEST)


            
            except User.DoesNotExist:
                
                self.track_verify_attempt(request=request, success=False)

                return Response({"error":"Invalid or Expired Token"},status=status.HTTP_404_NOT_FOUND)

        


class UserLoginView(APIView):


    
    def track_login_attempt(self, request, user=None, success=False):

        ip_address = get_client_ip(request=request)

        # Delete the Succeccful LoginAttempt of the IP Adress if ever before

        LoginAttempt.objects.filter(ip_address=ip_address, success=True).delete()
        
        # Create a new Failed LoginAttempt of the IP Address

        LoginAttempt.objects.create(user=user, ip_address=ip_address, success=success)

    
    def delete_attempts_before_threshold(self, ip_address, duration_minutes):

        time_threshold = timezone.now() - timedelta(seconds=duration_minutes)

        # Delete the Older Failed Login Attempts becuase they are no more revelant

        LoginAttempt.objects.filter(ip_address=ip_address, success=False, timestamp__lte=time_threshold).delete()

    
    def too_many_attempts(self, request, duration_minutes=15, max_attempts=5):
        
        ip_address = get_client_ip(request)

        time_threshold = timezone.now() - timedelta(seconds=duration_minutes)

        delete_old_attempts_thred = Thread(target=self.delete_attempts_before_threshold,args=(ip_address,duration_minutes))

        delete_old_attempts_thred.start()

        # Count failed login attempts for the IP in the given duration
        failed_attempts = LoginAttempt.objects.filter(ip_address=ip_address, success=False, timestamp__gte=time_threshold).count()

        return failed_attempts >= max_attempts
    

    def post(self, request):

        if self.too_many_attempts(request=request):

            return Response({"error":"Too many Login Requests. Try Again Later"},status=status.HTTP_429_TOO_MANY_REQUESTS)

        email = request.data.get("email")
        password = request.data.get("password")

        user = authenticate(username=email,password=password)

        if user is not None:

            self.track_login_attempt(request=request, user=user, success=True)

            if user.is_two_factor_authentication_enabled:

                return Response({"is_restricted_account" : user.is_two_factor_authentication_enabled, "email" : user.email})
            
            else:

                tokens = generate_user_tokens(user=user)

                clear_failed_attempts_thread = Thread(target=clean_failed_attempts,args=(request, "login-attempt"))

                clear_failed_attempts_thread.start()

                email_thread = Thread(
                    target=send_stylized_email,
                    args=(
                        user.email,
                        "New Login Detected",
                        "account_login_notify.html",
                        {
                            'username': user.username,
                            'login_time': datetime.now().strftime("%B %d, %Y at %I:%M %p")
                        }
                    )
                )

                email_thread.start()

                response = Response({"msg":"Login Successful", "store_date" : datetime.now().strftime("%Y-%m-%d"), "store_time" : str(datetime.now().strftime("%H:%M:%S"))},status=status.HTTP_200_OK)

                return set_tokens_and_expiry(response_object=response, tokens=tokens)

            # return Response({"msg":"Login Successful", "access":tokens['access'], "refresh":tokens['refresh'], "store_date" : str(datetime.now().date()), "store_time" : str(datetime.now().time())})
        
        else:
            
            self.track_login_attempt(request=request, success=False)

            return Response({"error":"Invalid Credentials"}, status=status.HTTP_400_BAD_REQUEST)





class SetTwoFactorAuthenticationPIN(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        user = request.user
        incoming_pin = request.data.get("two_factor_pin",None)

        if incoming_pin is not None or len(incoming_pin) == 5:

            try:

                user.is_two_factor_authentication_enabled = True
                
                user.set_two_factor_pin(raw_pin=incoming_pin)

                user.save()

                return Response(f"Two Factor PIN is Set", status=status.HTTP_200_OK)
            
            except Exception as e:

                return Response(f"{e}",status=status.HTTP_400_BAD_REQUEST)

        else:

            return Response("PIN must be provided and must have 5 numbers", status=status.HTTP_400_BAD_REQUEST)




class TwoFactorPINVerify(APIView):


    def track_login_attempt(self, request, user=None, success=False):

        ip_address = get_client_ip(request=request)

        # Delete the Succeccful LoginAttempt of the IP Adress if ever before

        TwoStepVerificationAttempt.objects.filter(ip_address=ip_address, success=True).delete()
        
        # Create a new Failed LoginAttempt of the IP Address

        TwoStepVerificationAttempt.objects.create(user=user, ip_address=ip_address, success=success)

    
    def delete_attempts_before_threshold(self, ip_address, duration_minutes):

        time_threshold = timezone.now() - timedelta(seconds=duration_minutes)

        # Delete the Older Failed Login Attempts becuase they are no more revelant

        TwoStepVerificationAttempt.objects.filter(ip_address=ip_address, success=False, timestamp__lte=time_threshold).delete()


    def too_many_attempts(self, request, duration_minutes=15, max_attempts=5):
        
        ip_address = get_client_ip(request)

        time_threshold = timezone.now() - timedelta(seconds=duration_minutes)

        delete_old_attempts_thred = Thread(target=self.delete_attempts_before_threshold,args=(ip_address,duration_minutes))

        delete_old_attempts_thred.start()

        # Count failed login attempts for the IP in the given duration
        failed_attempts = TwoStepVerificationAttempt.objects.filter(ip_address=ip_address, success=False, timestamp__gte=time_threshold).count()

        return failed_attempts >= max_attempts
    


    def post(self, request):

        if self.too_many_attempts(request=request):

            return Response({"error":"Too many Attempts. Try Again Later"},status=status.HTTP_429_TOO_MANY_REQUESTS)
        

        incoming_pin = request.data.get("two_step_pin", None)
        incoming_user_email = request.data.get("user_email", None)

        try:
        
            user = User.objects.get(email=incoming_user_email)

        except Exception as e:

            return Response(f"{e}",status=status.HTTP_400_BAD_REQUEST)

        if incoming_pin is not None:

            result = user.verify_two_factor_pin(raw_pin=incoming_pin)

            if result:
                
                self.track_login_attempt(request=request, user=user, success=True)

                clear_failed_attempts_thread = Thread(target=clean_failed_attempts,args=(request, "pin-attempt"))

                clear_failed_attempts_thread.start()

                tokens = generate_user_tokens(user=user)

                response = Response({"msg":"PIN Verified", "store_date" : datetime.now().strftime("%Y-%m-%d"), "store_time" : str(datetime.now().strftime("%H:%M:%S"))},status=status.HTTP_200_OK)

                return set_tokens_and_expiry(response_object=response, tokens=tokens)
            
            else:

                self.track_login_attempt(request=request, success=False)

                return Response("Wrong PIN", status=status.HTTP_400_BAD_REQUEST)

        else:

            return Response("No PIN was provided", status=status.HTTP_400_BAD_REQUEST)
        



class UserLogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        incoming_refresh_token = request.COOKIES.get('refresh', None)

        if incoming_refresh_token:
            try:
                token = RefreshToken(incoming_refresh_token)
                token.blacklist()

                response = Response({"msg": "Logout Success"}, status=status.HTTP_200_OK)
                response.delete_cookie("refresh")
                response.delete_cookie("access")

                return response

            except Exception as e:
                return Response({"msg": "Logout failed"}, status=status.HTTP_400_BAD_REQUEST)
        
        # Return a response if the refresh token is not present
        return Response({"msg": "Refresh token not found"}, status=status.HTTP_400_BAD_REQUEST)




class RefreshUserTokens(APIView):

    permission_classes = [AllowAny]

    def get(self, request):

        incoming_refresh_token = request.COOKIES.get("refresh",None)

        if incoming_refresh_token:

            try:

                token = RefreshToken(token=incoming_refresh_token)

            except TokenError:

                return Response("Invalid or Expired Token")

            user_id = token['user_id']

            token_exp_timestamp = token['exp']

            user = User.objects.get(id=user_id)
            

            if int(datetime.now().timestamp()) < token_exp_timestamp:

                if token_exp_timestamp < (datetime.now() + timedelta(minutes=50)).timestamp():

                    token.blacklist()

                    response = Response({"msg":"Refreshed Both Tokens", "store_date" : datetime.now().strftime("%Y-%m-%d"), "store_time" : str(datetime.now().strftime("%H:%M:%S"))},status=status.HTTP_200_OK)

                    tokens = generate_user_tokens(user=user)

                    return set_tokens_and_expiry(response_object=response, tokens=tokens)

                    # return Response({"msg" : "Refreshed Both Tokens", "refresh_token" : tokens['refresh'], "access_token" : tokens['access'], "store_date" : str(datetime.now().date), "store_time" : str(datetime.now().time())})
                
                
                else:

                    try:

                        access_token = AccessToken.for_user(user=user)

                        response = Response({"msg" : "Updated Access Token", "store_date" : datetime.now().strftime("%Y-%m-%d"), "store_time" : str(datetime.now().strftime("%H:%M:%S"))}, status=status.HTTP_200_OK)

                        access_token_expiry = datetime.now(dt_timezone.utc) + timedelta(minutes=10)

                        response.set_cookie('access' , access_token , expires=access_token_expiry, secure=True, httponly=True , samesite='None')

                        # return Response({"access_token" : str(access_token), "store_date" : str(datetime.now().date()), "store_time" : str(datetime.now().time())}, status = status.HTTP_200_OK)

                        return response
                    
                    except TokenError :

                        return Response("Invalid Token", status=status.HTTP_400_BAD_REQUEST)


            else:

                return Response("Refresh Token Expired. Please Login Again", status=status.HTTP_401_UNAUTHORIZED)
        
        else:

            return Response("Token Not Provided",status=status.HTTP_400_BAD_REQUEST)



class PasswordResetView(APIView):

    def post(self, request):

        incoming_email = request.data.get('user_email',None)

        if incoming_email is not None:

            try:

                user = User.objects.get(email=incoming_email)

                assign_verification_token(user=user)
                assign_otp(user=user)

                email_thread = Thread(target=send_stylized_email,args=(user.email,"Reset Your Password",'otp.html',{'username':user.username,'otp':user.otp}))

                email_thread.start()

                return Response({"verification_token":user.verification_token},status=status.HTTP_200_OK)
            
            except User.DoesNotExist:

                return Response("Unable To Find User",status=status.HTTP_400_BAD_REQUEST)
            
        else:
            
            return Response("User Email Was Not Provided",status=status.HTTP_400_BAD_REQUEST)



class VerifyResetPasswordOTP(APIView):

    def track_verify_attempt(self, request, user=None, success=False):

        ip_address = get_client_ip(request=request)

        # Delete the Succeccful LoginAttempt of the IP Adress if ever before

        OTPVerifyAttempt.objects.filter(ip_address=ip_address, success=True).delete()
        
        # Create a new Failed LoginAttempt of the IP Address

        OTPVerifyAttempt.objects.create(user=user, ip_address=ip_address, success=success)

    
    def delete_attempts_before_threshold(self, ip_address, duration_minutes):

        time_threshold = timezone.now() - timedelta(minutes=duration_minutes)

        # Delete the Older Failed Login Attempts becuase they are no more revelant

        OTPVerifyAttempt.objects.filter(ip_address=ip_address, success=False, timestamp__lte=time_threshold).delete()

    
    def too_many_attempts(self, request, duration_minutes=15, max_attempts=5):
        
        ip_address = get_client_ip(request)

        time_threshold = timezone.now() - timedelta(minutes=duration_minutes)

        delete_old_attempts_thred = Thread(target=self.delete_attempts_before_threshold,args=(ip_address,duration_minutes))

        delete_old_attempts_thred.start()

        # Count failed login attempts for the IP in the given duration
        failed_attempts = OTPVerifyAttempt.objects.filter(ip_address=ip_address, success=False, timestamp__gte=time_threshold).count()

        return failed_attempts >= max_attempts
    
    def post(self, request):

        if self.too_many_attempts(request=request):

            return Response({"error":"Too many Verification Requests. Try Again Later"},status=status.HTTP_429_TOO_MANY_REQUESTS)

        incoming_verification_token = request.data.get("verification_token",None)
        incoming_otp = request.data.get("otp",None)

        if incoming_verification_token is not None:

            try:

                user = User.objects.get(verification_token=incoming_verification_token)

                if user.verification_token_expiry is not None and user.verification_token_expiry > timezone.now():

                    if user.otp_expiry is not None and user.otp_expiry > timezone.now() and user.otp == incoming_otp:

                        self.track_verify_attempt(request=request, user=user, success=True)

                        user.is_active = True
                        user.otp = None
                        user.otp_expiry = None
                        assign_verification_token(user=user)
            
                        clear_failed_attempts_thread = Thread(target=clean_failed_attempts,args=(request, "otp-attempt"))

                        clear_failed_attempts_thread.start()

                        user.save()

                        # tokens = generate_user_tokens(user=user)

                        return Response({"verification_token":user.verification_token},status=status.HTTP_200_OK)
                    
                    else:

                        self.track_verify_attempt(request=request, success=False)

                        return Response({"error":"Invalid or Expired OTP"},status=status.HTTP_400_BAD_REQUEST)
                    
                else:

                    self.track_verify_attempt(request=request, success=False)

                    return Response({"error":"Invalid or Expired Token"}, status=status.HTTP_400_BAD_REQUEST)


            
            except User.DoesNotExist:
                
                self.track_verify_attempt(request=request, success=False)

                return Response({"error":"Invalid or Expired Token"},status=status.HTTP_404_NOT_FOUND)



class ChangeUserPassword(APIView):

    def post(self, request):

        incoming_verification_token = request.data.get('verification_token',None)
        incoming_pass_1 = request.data.get('pass1',None)
        incoming_pass_2 = request.data.get("pass2",None)

        if incoming_verification_token is not None:

            try:

                user = User.objects.get(verification_token=incoming_verification_token)

                if user.verification_token_expiry is not None and user.verification_token_expiry > timezone.now():

                    if incoming_pass_1 is not None or incoming_pass_2 is not None:

                        if len(str(incoming_pass_1)) > 8:

                            if str(incoming_pass_1) == str(incoming_pass_2):

                                user.set_password(incoming_pass_1)

                                user.save()

                                return Response("Password Changed Successfully",status=status.HTTP_200_OK)
                            
                            else:

                                return Response("Passwords Must Match",status=status.HTTP_400_BAD_REQUEST)
                            
                        else:

                            return Response("Password Must Contain 8 Characters",status=status.HTTP_400_BAD_REQUEST)
                    
                    else:

                        return Response("Passwords Data Was Not Provided",status=status.HTTP_400_BAD_REQUEST)

                else:

                    return Response("Invalid or Expired Token",status=status.HTTP_400_BAD_REQUEST)
            
            except User.DoesNotExist:

                return Response("Invalid Token",status=status.HTTP_400_BAD_REQUEST)
        
        else:

            return Response("Token Was Not Provided",status=status.HTTP_400_BAD_REQUEST)




class AddModifyCardDetails(APIView):

    permission_classes = [IsAuthenticated]

    def post(self, request):

        frontend_data = {"card_holder_name" : request.data.get('card_holder_name',None), "card_number" : request.data.get('card_number',None), "expiry_date" : request.data.get('expiry_date',None), "card_type" : request.data.get('card_type',None)}

        fields_validation = check_frontend_fields(fields=frontend_data)

        if not fields_validation[0]:

            return Response({f"{fields_validation[1]}":f"{fields_validation[2]}"}, status=status.HTTP_400_BAD_REQUEST)
        
        if len(frontend_data['card_number']) < 10:

            return Response("Card Number Must Have 10 digits",status=status.HTTP_400_BAD_REQUEST)
        
        if CardDetails.objects.filter(card_number=frontend_data['card_number']).exists():

            return Response("Card with the Number Provided Already Exists",status=status.HTTP_400_BAD_REQUEST)
        
        if frontend_data['card_type'] != "credit" and frontend_data['card_type'] != "debit":

            return Response("Card Can Be 'credit' or 'debit'",status=status.HTTP_400_BAD_REQUEST)

        
        new_card = CardDetails.objects.create(user=request.user, card_holder_name=frontend_data['card_holder_name'], card_number=frontend_data['card_number'], expiry_date=frontend_data['expiry_date'], card_type=frontend_data['card_type'])

        new_payment_method = PaymentMethod.objects.create(user=request.user, card_details=new_card)

        if len(request.user.card_details.all()) == 1:

            new_payment_method.is_default = True

            new_payment_method.save()

        return Response("Card Details Are Saved",status=status.HTTP_200_OK)




class CheckCookiesSetting(APIView):
    def get(self, request):
        # Set up the response for testing
        response = Response("Setting The Cookies.....", status=200)

        # Define cookie expiry time
        cookie_expiry = timezone.now() + timedelta(days=5)

        # Set a test cookie
        response.set_cookie(
            'example_cookie',
            value='1234567',
            expires=cookie_expiry,
            secure=True,         # Secure cookie (required for SameSite=None)
            httponly=True,       # Ensures cookie isn't accessible via JavaScript
            samesite='None',     # Required for cross-origin requests
        )


        return response



# ------------------ OTHER MOST IMPORTANT FUNCTIONS ---------------------------


def send_stylized_email(
    user_email: str,
    subject: str,
    template_name: str,
    arguments_for_template: dict
):

    html_message = render_to_string(
        template_name,
        arguments_for_template
    )

    html_message = transform(html_message)

    message = MIMEMultipart("alternative")

    message["From"] = settings.GMAIL_SENDER_EMAIL
    message["To"] = user_email
    message["Subject"] = subject

    message.attach(
        MIMEText(
            html_message,
            "html",
            "utf-8"
        )
    )

    raw_message = base64.urlsafe_b64encode(
        message.as_bytes()
    ).decode()

    credentials = Credentials(
        token=None,
        refresh_token=settings.GMAIL_REFRESH_TOKEN,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.GOOGLE_CLIENT_ID,
        client_secret=settings.GOOGLE_CLIENT_SECRET,
        scopes=[
            "https://www.googleapis.com/auth/gmail.send"
        ],
    )

    credentials.refresh(Request())

    service = build(
        "gmail",
        "v1",
        credentials=credentials
    )

    response = service.users().messages().send(
        userId="me",
        body={"raw": raw_message}
    ).execute()

    print("GMAIL API RESPONSE:", response, flush=True)

    return response

def assign_verification_token(user : User):

    """
    Assigns User A Unique Verification Token
    """

    while True:

        generated_token = secrets.token_urlsafe(32)

        try:

            with transaction.atomic():

                user.verification_token = generated_token

                user.verification_token_expiry = timezone.now() + timedelta(minutes=5)

                user.save()

                return generated_token
            

        except IntegrityError:

            continue



def assign_otp(user : User):

    """
    Assigns User A Unique OTP
    """

    while True:

        generated_otp = "".join(random.choices(population=string.digits,k=4))

        try:

            with transaction.atomic():

                user.otp = generated_otp

                user.otp_expiry = timezone.now() + timedelta(minutes=5)

                user.save()

                break

        except IntegrityError:

            continue



def generate_user_tokens(user : User) -> dict:

    """
    Generates and Returns The Access and Refresh Tokens for The User using SimpleJWT
    """

    tokens = RefreshToken.for_user(user)

    return {
        'refresh' : str(tokens),
        'access' : str(tokens.access_token),
    }



def set_tokens_and_expiry(response_object, tokens : dict):

    """
    Set Tokens in The Cookies of Browser and Returns a Response Object
    """

    refresh_token_expiry = datetime.now(dt_timezone.utc) + timedelta(days=5)
    access_token_expiry = datetime.now(dt_timezone.utc) + timedelta(minutes=10)

    response_object.set_cookie('refresh', tokens['refresh'], expires=refresh_token_expiry, secure=True, httponly=True, samesite='None')
    response_object.set_cookie('access', tokens['access'], expires=access_token_expiry, secure=True, httponly=True, samesite='None')

    return response_object



def clean_failed_attempts(request, attempt_type : str):

    """
    Clears All The Failed Attempts
    """

    client_ip = get_client_ip(request=request)

    match attempt_type :

        case "login-attempt":

            LoginAttempt.objects.filter(ip_address = client_ip, success = False).delete()

            return True
        
        case "otp-attempt":

            OTPVerifyAttempt.objects.filter(ip_address = client_ip, success = False).delete()

            return True
        
        case "pin-attempt":

            TwoStepVerificationAttempt.objects.filter(ip_address = client_ip, success = False).delete()

            return True
        
        case _:

            return False



def get_client_ip(request):
    
    """
    Extracts and Returns The IP Address of Request
    """

    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')

    if x_forwarded_for:

        ip = x_forwarded_for.split(",")[0]

    else:

        ip = request.META.get("REMOTE_ADDR")

    return ip



def make_user_password():
    
    alphabet = string.ascii_letters + string.digits
    
    password = ''.join(secrets.choice(alphabet) for i in range(20))
    
    return str(password)