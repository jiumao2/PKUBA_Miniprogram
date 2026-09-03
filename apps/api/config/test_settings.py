from .settings import *  # noqa: F403

# Tests exercise password creation, verification and secrecy, not production hash cost.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
