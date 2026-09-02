from django.test import Client, override_settings


@override_settings(
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
)
def test_security_middleware_trusts_slot_forwarded_https_without_self_redirect():
    client = Client()

    secure = client.get(
        "/api/v1/health/live",
        HTTP_X_FORWARDED_PROTO="https",
    )
    insecure = client.get("/api/v1/health/live")

    assert secure.status_code == 200
    assert insecure.status_code == 301
    assert insecure["Location"] == "https://testserver/api/v1/health/live"
