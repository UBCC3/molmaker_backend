# auth.py
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt
import requests
import os

from dotenv import load_dotenv
load_dotenv()


AUTH0_DOMAIN = os.getenv("AUTH0_DOMAIN")
API_AUDIENCE = os.getenv("API_AUDIENCE")
ALGORITHMS = os.getenv("ALGORITHMS", "RS256").split(",")

http_bearer = HTTPBearer()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(http_bearer)):
    token = credentials.credentials
    print(token)
    try:
        # Get public keys from Auth0
        jwks_url = f"https://{AUTH0_DOMAIN}/.well-known/jwks.json"
        print(AUTH0_DOMAIN)
        jwks = requests.get(jwks_url).json()
        unverified_header = jwt.get_unverified_header(token)

        rsa_key = next(
            (key for key in jwks["keys"] if key["kid"] == unverified_header["kid"]),
            None
        )

        if rsa_key:
            payload = jwt.decode(
                token,
                key={
                    "kty": rsa_key["kty"],
                    "kid": rsa_key["kid"],
                    "use": rsa_key["use"],
                    "n": rsa_key["n"],
                    "e": rsa_key["e"]
                },
                algorithms=ALGORITHMS,
                audience=API_AUDIENCE,
                issuer=f"https://{AUTH0_DOMAIN}/"
            )
            return payload  # includes `sub`, `email`, etc.

    except Exception as e:
        raise HTTPException(status_code=401, detail="Invalid access token")

    raise HTTPException(status_code=401, detail="Authorization failed")
