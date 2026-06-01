import pytest

class TestGetUserSub:
    def test_valid_payload_returns_user_sub(self):
        """
        Should return user_sub with valid_payload.
        """
        from utils import get_user_sub

        payload = {
            "sub": "auth0|abc456efg",
            "iss": "https://your-tenant.auth0.com/",
            "aud": "https://your-api.com",
            "iat": 1716230400,
            "exp": 1716234000,
        }

        user_sub = get_user_sub(payload)

        assert user_sub == "auth0|abc456efg"

    def test_not_dict_raise_error(self):
        """
        When payload is not a dict, it should raise an error
        """
        from utils import get_user_sub
        from fastapi import HTTPException

        payload = [
            "sub", "auth0|abc456efg",
            "iss", "https://your-tenant.auth0.com/",
            "aud", "https://your-api.com",
            "iat", 1716230400,
            "exp", 1716234000,
        ]

        with pytest.raises(HTTPException) as exc_info:
            get_user_sub(payload)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"

    def test_no_sub_raise_error(self):
        """
        When payload does not store sub, it should raise an error
        """
        from utils import get_user_sub
        from fastapi import HTTPException

        payload = {
            "iss": "https://your-tenant.auth0.com/",
            "aud": "https://your-api.com",
            "iat": 1716230400,
            "exp": 1716234000,
        }

        with pytest.raises(HTTPException) as exc_info:
            get_user_sub(payload)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"

    def test_empty_sub_raise_error(self):
        """
        When payload does not store sub, it should raise an error
        """
        from utils import get_user_sub
        from fastapi import HTTPException

        payload = {
            "sub": "",
            "iss": "https://your-tenant.auth0.com/",
            "aud": "https://your-api.com",
            "iat": 1716230400,
            "exp": 1716234000,
        }

        with pytest.raises(HTTPException) as exc_info:
            get_user_sub(payload)

        assert exc_info.value.status_code == 401
        assert exc_info.value.detail == "Unauthorized"