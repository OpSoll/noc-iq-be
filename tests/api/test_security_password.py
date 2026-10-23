import pytest
from app.core.security import validate_password_policy

def test_valid_strong_password():
    is_valid, msg = validate_password_policy("SecurePass@123")
    assert is_valid is True
    assert msg == "Password is valid."

@pytest.mark.parametrize(
    "too_short_password",
    [
        "Sec@1",
        "Ab1!",
        "Short1@",
    ],
)
def test_password_minimum_length_boundary(too_short_password):
    is_valid, msg = validate_password_policy(too_short_password)
    assert is_valid is False
    assert "at least 8 characters" in msg

def test_password_maximum_length_boundary():
    # 128 valid characters is allowed, 129 should fail
    valid_long = "A" * 120 + "b1@!"
    assert validate_password_policy(valid_long)[0] is True

    too_long = "A" * 125 + "b1@!"
    is_valid, msg = validate_password_policy(too_long)
    assert is_valid is False
    assert "not exceed 128 characters" in msg

def test_missing_uppercase_letter():
    is_valid, msg = validate_password_policy("securepass@123")
    assert is_valid is False
    assert "uppercase letter" in msg

def test_missing_lowercase_letter():
    is_valid, msg = validate_password_policy("SECUREPASS@123")
    assert is_valid is False
    assert "lowercase letter" in msg

def test_missing_number():
    is_valid, msg = validate_password_policy("SecurePassword!")
    assert is_valid is False
    assert "number" in msg

def test_missing_special_character():
    is_valid, msg = validate_password_policy("SecurePass123")
    assert is_valid is False
    assert "special character" in msg

def test_combined_failures():
    # Fails length, number, and special character
    is_valid, msg = validate_password_policy("abc")
    assert is_valid is False
    assert "at least 8 characters" in msg