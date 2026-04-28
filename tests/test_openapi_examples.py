from app.main import app


def test_openapi_schema_includes_auth_examples():
    schema = app.openapi()
    assert schema["components"]["schemas"]["LoginRequest"]["example"]["email"] == "user@example.com"
    assert schema["components"]["schemas"]["RegisterRequest"]["example"]["password"] == "Password123!"


def test_openapi_schema_includes_outage_and_payment_examples():
    schema = app.openapi()
    assert schema["components"]["schemas"]["OutageCreate"]["example"]["site_name"] == "Example Site"
    assert schema["components"]["schemas"]["PaymentTransaction"]["example"]["amount"] == 150.0
    assert schema["components"]["schemas"]["WebhookCreate"]["example"]["url"] == "https://example.com/webhook"
