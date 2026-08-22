import uvicorn


def main() -> None:
    uvicorn.run(
        "vms_portal.web:create_app_from_env", factory=True, host="0.0.0.0", port=8000
    )
