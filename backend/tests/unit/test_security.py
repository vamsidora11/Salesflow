import uuid

import jwt
import pytest

from app.core.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    generate_api_key,
    hash_api_key,
    hash_password,
    verify_password,
)


def test_api_key_hash_is_sha256_hex_and_key_is_not_stored() -> None:
    key = generate_api_key()
    digest = hash_api_key(key)
    assert key.startswith("sf_")
    assert len(digest) == 64
    assert key not in digest
    assert hash_api_key(key) == digest
    assert generate_api_key() != key


def test_password_roundtrip_and_salting() -> None:
    a, b = hash_password("s3cret"), hash_password("s3cret")
    assert a != b
    assert verify_password("s3cret", a)
    assert not verify_password("wrong", a)
    assert not verify_password("s3cret", "garbage")


def test_jwt_roundtrip() -> None:
    user, tenant = uuid.uuid4(), uuid.uuid4()
    token = create_access_token(
        secret="k", user_id=user, tenant_id=tenant, role="admin", expires_minutes=5
    )
    claims = decode_access_token(token, "k")
    assert claims["sub"] == str(user)
    assert claims["tenant_id"] == str(tenant)
    assert claims["role"] == "admin"


def test_jwt_rejects_expired_tampered_and_wrong_secret() -> None:
    kwargs = {"user_id": uuid.uuid4(), "tenant_id": uuid.uuid4(), "role": "viewer"}
    expired = create_access_token(secret="k", expires_minutes=-1, **kwargs)  # type: ignore[arg-type]
    good = create_access_token(secret="k", expires_minutes=5, **kwargs)  # type: ignore[arg-type]
    with pytest.raises(InvalidTokenError):
        decode_access_token(expired, "k")
    with pytest.raises(InvalidTokenError):
        decode_access_token(good, "other")
    with pytest.raises(InvalidTokenError):
        decode_access_token(good[:-2] + "xx", "k")
    # alg=none must never be accepted
    forged = jwt.encode({"sub": "x", "exp": 9999999999}, key="", algorithm="none")
    with pytest.raises(InvalidTokenError):
        decode_access_token(forged, "k")
