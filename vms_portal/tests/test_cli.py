from vms_portal.cli import build_secret


def test_secret_builder_hashes_passwords_and_never_returns_plaintext() -> None:
    result = build_secret("admin-password", "user-password", session_key="z" * 64)

    assert result["auth_version"] == 1
    assert result["session_key"] == "z" * 64
    assert {account["role"] for account in result["accounts"]} == {"admin", "user"}
    assert all(
        account["password_hash"].startswith("$argon2id$")
        for account in result["accounts"]
    )
    assert "admin-password" not in str(result)
    assert "user-password" not in str(result)
