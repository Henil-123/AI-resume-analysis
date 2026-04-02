"""
Auth Module — Basic JWT authentication for recruiters.

Features:
- Signup and login with hashed passwords
- JWT token generation and verification
- @require_auth decorator for protected endpoints
- Local JSON storage for users (MongoDB optional)
"""

import os
import json
import uuid
import hashlib
import hmac
import base64
import time
import logging
from functools import wraps
from flask import request, jsonify

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────
SECRET_KEY = os.getenv("JWT_SECRET", "resume-analyzer-dev-secret-change-in-production")
TOKEN_EXPIRY = 86400  # 24 hours
USERS_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "users.json")


# ── Simple password hashing (no bcrypt dependency) ───────────────

def hash_password(password):
    """Hash a password using SHA-256 with salt."""
    salt = uuid.uuid4().hex
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{hashed}"


def verify_password(password, stored_hash):
    """Verify a password against a stored hash."""
    salt, hashed = stored_hash.split(":")
    return hashlib.sha256((salt + password).encode()).hexdigest() == hashed


# ── Simple JWT (no PyJWT dependency) ─────────────────────────────

def _b64encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode()


def _b64decode(data):
    padding = 4 - len(data) % 4
    data += '=' * padding
    return base64.urlsafe_b64decode(data)


def create_token(user_id, email):
    """Create a JWT token."""
    header = _b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64encode(json.dumps({
        "user_id": user_id,
        "email": email,
        "exp": int(time.time()) + TOKEN_EXPIRY,
        "iat": int(time.time())
    }).encode())

    signature_input = f"{header}.{payload}"
    signature = hmac.new(
        SECRET_KEY.encode(), signature_input.encode(), hashlib.sha256
    ).digest()
    sig_encoded = _b64encode(signature)

    return f"{header}.{payload}.{sig_encoded}"


def verify_token(token):
    """Verify a JWT token. Returns payload dict or None."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header, payload, signature = parts

        # Verify signature
        signature_input = f"{header}.{payload}"
        expected_sig = hmac.new(
            SECRET_KEY.encode(), signature_input.encode(), hashlib.sha256
        ).digest()
        expected_sig_encoded = _b64encode(expected_sig)

        if not hmac.compare_digest(signature, expected_sig_encoded):
            return None

        # Decode payload
        payload_data = json.loads(_b64decode(payload))

        # Check expiry
        if payload_data.get("exp", 0) < time.time():
            return None

        return payload_data

    except Exception as e:
        logger.warning(f"Token verification failed: {e}")
        return None


# ── User storage ─────────────────────────────────────────────────

def _load_users():
    if not os.path.exists(USERS_DB_PATH):
        return []
    with open(USERS_DB_PATH, "r") as f:
        return json.load(f)


def _save_users(users):
    os.makedirs(os.path.dirname(USERS_DB_PATH), exist_ok=True)
    with open(USERS_DB_PATH, "w") as f:
        json.dump(users, f, indent=2)


def create_user(email, password, name=""):
    """Create a new user. Returns (user_dict, error_string)."""
    users = _load_users()

    # Check if email already exists
    if any(u["email"] == email for u in users):
        return None, "Email already registered"

    user = {
        "id": str(uuid.uuid4())[:8],
        "email": email,
        "name": name,
        "password_hash": hash_password(password),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    }

    users.append(user)
    _save_users(users)

    # Return user without password hash
    return {k: v for k, v in user.items() if k != "password_hash"}, None


def authenticate_user(email, password):
    """Authenticate a user. Returns (user_dict, error_string)."""
    users = _load_users()

    for user in users:
        if user["email"] == email:
            if verify_password(password, user["password_hash"]):
                return {k: v for k, v in user.items() if k != "password_hash"}, None
            else:
                return None, "Invalid password"

    return None, "User not found"


# ── Flask decorator ──────────────────────────────────────────────

def require_auth(f):
    """Decorator to protect endpoints with JWT auth."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Missing or invalid Authorization header"}), 401

        token = auth_header.split(" ", 1)[1]
        payload = verify_token(token)

        if not payload:
            return jsonify({"error": "Invalid or expired token"}), 401

        # Attach user info to request
        request.user = payload
        return f(*args, **kwargs)

    return decorated
