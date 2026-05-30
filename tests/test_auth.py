import pytest
from unittest.mock import patch, MagicMock
import uuid
from datetime import datetime, timezone

from conftest import make_auth0_payload
from models import Job

# Helper to mock verify_token as a FastAPI dependency override
def mock_verify_token(payload):
    """
    Returns a function that can be used to override verify_token dependency.
    """
    from auth import verify_token
    from main import app

    def _override():
        return payload
    
    app.dependency_overrides[verify_token] = _override

# 1. verify_token unit tests (the function itself, not via HTTP)

class TestVerifyTokenUnit:
    
    @patch("auth.requests.get")
    @patch("auth.jwt.get_unverified_header")
    @patch("auth.jwt.decode")
    def test_valid_token_returns_payload(self, mock_decode, mock_header, mock_requests_get):
        """
        A well-formed token with a matching key returns the decoded payload.
        """
        mock_requests_get.return_value.json.return_value = {
            "keys": [{
                "kid": "test-key-id",
                "kty": "RSA",
                "use": "sig",
                "n": "some-n",
                "e": "AQAB"
            }]
        }
        mock_header.return_value = {"kid": "test-key-id"}
        mock_decode.return_value = {"sub": "auth0|testuser"}

        from auth import verify_token
        from fastapi.security import HTTPAuthorizationCredentials

        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake.jwt.token")
        result = verify_token(credentials)

        assert result == {"sub": "auth0|testuser"}

    @patch("auth.requests.get")
    @patch("auth.jwt.get_unverified_header")
    def test_no_matching_key_raises_401(self, mock_header, mock_requests_get):
        """
        Token with a kid that doesn't match any JWKS key raises 401.
        """
        from fastapi import HTTPException
        from auth import verify_token
        from fastapi.security import HTTPAuthorizationCredentials

        mock_requests_get.return_value.json.return_value = {"keys": [{"kid": "other-key"}]}
        mock_header.return_value = {"kid": "missing-key-id"}

        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="fake.jwt.token")

        with pytest.raises(HTTPException) as exc_info:
            verify_token(credentials)

        assert exc_info.value.status_code == 401
        assert "Authorization failed" == exc_info.value.detail

    @patch("auth.requests.get")
    @patch("auth.jwt.get_unverified_header")
    @patch("auth.jwt.decode")
    def test_expired_token_raises_401(self, mock_decode, mock_header, mock_requests_get):
        """
        An expired token raises 401.
        """
        from jose import ExpiredSignatureError
        from fastapi import HTTPException
        from auth import verify_token
        from fastapi.security import HTTPAuthorizationCredentials

        mock_requests_get.return_value.json.return_value = {
            "keys": [{
                "kid": "test-key-id",
                "kty": "RSA",
                "use": "sig",
                "n": "n",
                "e": "e"
            }]
        }
        mock_header.return_value = {"kid": "test-key-id"}
        mock_decode.side_effect = ExpiredSignatureError("Token expired")

        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="expired.jwt.token")

        with pytest.raises(HTTPException) as exc_info:
            verify_token(credentials)

        assert exc_info.value.status_code == 401
        assert "Invalid access token" == exc_info.value.detail