from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.throttling import AnonRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    scope = "login"


class LoginView(ObtainAuthToken):
    """Exchange a username and password for an API token. Rate limited per IP address."""

    throttle_classes = [LoginRateThrottle]
