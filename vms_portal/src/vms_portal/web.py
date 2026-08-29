from __future__ import annotations

import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .audit import AuditEvent, AuditLogger
from .assignments import AssignmentRepository
from .config import Settings
from .costs import CostService
from .ec2 import Ec2Service, VmError
from .secrets import SecretCache, SecretUnavailable
from .sessions import (
    InvalidSession,
    LoginLimiter,
    SessionIdentity,
    SessionManager,
    new_csrf_token,
    validate_csrf,
)

_ROOT = Path(__file__).parent
_INSTANCE_ID = re.compile(r"^i-[0-9a-f]{8,17}$")


def create_app(
    settings: Settings,
    *,
    secret_cache: Any | None = None,
    ec2_service: Any | None = None,
    cost_service: Any | None = None,
    assignment_repository: Any | None = None,
    audit_logger: AuditLogger | None = None,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    if secret_cache is None:
        secret_cache = SecretCache(
            boto3.client("secretsmanager", region_name=settings.aws_region),
            settings.auth_secret_id,
        )
    snapshot = secret_cache.load_startup()
    ec2_service = ec2_service or Ec2Service(
        boto3.client("ec2", region_name=settings.aws_region)
    )
    cost_service = cost_service or CostService(
        boto3.client("ce", region_name="us-east-1"),
        settings.cost_cache_seconds,
        settings.public_ipv4_hourly_usd,
    )
    assignment_repository = assignment_repository or AssignmentRepository(
        settings.assignments_db_path
    )
    audit_logger = audit_logger or AuditLogger(
        logging.getLogger("vms_portal.audit").warning
    )
    sessions = SessionManager(snapshot.session_key)
    limiter = LoginLimiter()
    templates = Jinja2Templates(directory=_ROOT / "templates")
    app = FastAPI(
        title="Windows VM Portal", docs_url=None, redoc_url=None, openapi_url=None
    )
    app.mount("/static", StaticFiles(directory=_ROOT / "static"), name="static")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        current = getattr(request.state, "identity", None)
        if current is not None and not getattr(
            request.state, "skip_session_refresh", False
        ):
            response.set_cookie(
                settings.session_cookie_name,
                sessions.refresh(current, clock()),
                secure=True,
                httponly=True,
                samesite="lax",
                max_age=28_800,
            )
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    def identity(request: Request) -> SessionIdentity | None:
        token = request.cookies.get(settings.session_cookie_name)
        if not token:
            return None
        try:
            current = secret_cache.snapshot_for_auth()
            result = sessions.validate(token, current.auth_version, clock())
            request.state.identity = result
            return result
        except (InvalidSession, SecretUnavailable):
            return None

    def render(
        request: Request, name: str, context: dict[str, Any], status_code: int = 200
    ):
        return templates.TemplateResponse(
            request=request,
            name=name,
            context={"request": request, **context},
            status_code=status_code,
        )

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            secret_cache.snapshot_for_auth()
            return {"status": "ready"}
        except SecretUnavailable:
            return HTMLResponse("not ready", status_code=503)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        token = new_csrf_token()
        response = render(request, "login.html", {"csrf_token": token, "error": None})
        response.set_cookie(
            "vms_portal_login_csrf", token, secure=True, httponly=True, samesite="lax"
        )
        return response

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        supplied_csrf = str(form.get("csrf_token", ""))
        if not validate_csrf(
            request.cookies.get("vms_portal_login_csrf", ""), supplied_csrf
        ):
            return HTMLResponse("Forbidden", status_code=403)
        source_ip = request.client.host if request.client else "unknown"
        if limiter.is_blocked(source_ip, clock()):
            return render(
                request,
                "login.html",
                {"csrf_token": supplied_csrf, "error": "登入暫時鎖定，請稍後再試。"},
                429,
            )
        record = secret_cache.verify_password(
            str(form.get("username", "")), str(form.get("password", ""))
        )
        request_id = str(uuid.uuid4())
        if record is None:
            limiter.register_failure(source_ip, clock())
            audit_logger.emit(
                AuditEvent("login.failed", "rejected", request_id, source_ip=source_ip)
            )
            return render(
                request,
                "login.html",
                {"csrf_token": supplied_csrf, "error": "帳號或密碼錯誤。"},
                401,
            )
        limiter.register_success(source_ip)
        current = secret_cache.snapshot_for_auth()
        session_token = sessions.issue(
            record.username, record.role, current.auth_version, clock()
        )
        session_identity = sessions.validate(
            session_token, current.auth_version, clock()
        )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            settings.session_cookie_name,
            session_token,
            secure=True,
            httponly=True,
            samesite="lax",
            max_age=28_800,
        )
        response.set_cookie(
            "vms_portal_session_csrf",
            session_identity.csrf_token,
            secure=True,
            httponly=False,
            samesite="lax",
            max_age=28_800,
        )
        response.delete_cookie("vms_portal_login_csrf")
        audit_logger.emit(
            AuditEvent(
                "login.succeeded",
                "accepted",
                request_id,
                record.username,
                record.role,
                source_ip,
            )
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        current = identity(request)
        if current is None:
            return RedirectResponse("/login", status_code=303)
        if current.role == "user":
            return render(
                request, "user.html", {"identity": current, "vm": None, "error": None}
            )
        vms = ec2_service.list_managed()
        costs = cost_service.get_costs(vms, datetime.now(UTC))
        assignments = assignment_repository.get_many(
            vm.instance_id for vm in vms
        )
        return render(
            request,
            "admin.html",
            {
                "identity": current,
                "vms": vms,
                "costs": costs,
                "assignments": assignments,
            },
        )

    @app.post("/logout")
    async def logout(request: Request):
        current = identity(request)
        if current is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if not validate_csrf(current.csrf_token, str(form.get("csrf_token", ""))):
            return HTMLResponse("Forbidden", status_code=403)
        request.state.skip_session_refresh = True
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(settings.session_cookie_name)
        response.delete_cookie("vms_portal_session_csrf")
        audit_logger.emit(
            AuditEvent(
                "logout", "accepted", str(uuid.uuid4()), current.username, current.role
            )
        )
        return response

    @app.post("/lookup", response_class=HTMLResponse)
    async def lookup(request: Request):
        current = identity(request)
        if current is None or current.role != "user":
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if not validate_csrf(current.csrf_token, str(form.get("csrf_token", ""))):
            return HTMLResponse("Forbidden", status_code=403)
        vm = None
        supplied_id = str(form.get("instance_id", "")).strip()
        if _INSTANCE_ID.fullmatch(supplied_id):
            vm = ec2_service.find_managed_by_instance_id(supplied_id)
        error = None if vm else "找不到符合條件的機器。"
        costs = {}
        if vm:
            costs = cost_service.get_costs([vm], datetime.now(UTC))
        return render(
            request,
            "user.html",
            {"identity": current, "vm": vm, "error": error, "costs": costs},
        )

    @app.post("/instances/{instance_id}/assignment")
    async def update_assignment(instance_id: str, request: Request):
        current = identity(request)
        if current is None:
            return RedirectResponse("/login", status_code=303)
        if current.role != "admin":
            return HTMLResponse("Forbidden", status_code=403)
        form = await request.form()
        if not validate_csrf(current.csrf_token, str(form.get("csrf_token", ""))):
            return HTMLResponse("Forbidden", status_code=403)
        vm = ec2_service.find_managed_by_instance_id(instance_id)
        if vm is None:
            return HTMLResponse("Not found", status_code=404)
        assignee = str(form.get("assignee", "")).strip()
        if len(assignee) > 200:
            return HTMLResponse("Invalid assignment", status_code=422)
        assignment_repository.upsert(
            instance_id,
            assignee,
            updated_by=current.username,
            updated_at=datetime.fromtimestamp(clock(), UTC),
        )
        audit_logger.emit(
            AuditEvent(
                "vm.assignment.updated",
                "succeeded",
                str(uuid.uuid4()),
                current.username,
                current.role,
                request.client.host if request.client else "unknown",
                instance_id,
                details={"assignee": assignee},
            )
        )
        return RedirectResponse("/", status_code=303)

    @app.post("/instances/{instance_id}/{action}")
    async def power(instance_id: str, action: str, request: Request):
        current = identity(request)
        if current is None:
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        if not validate_csrf(current.csrf_token, str(form.get("csrf_token", ""))):
            return HTMLResponse("Forbidden", status_code=403)
        if action not in {"start", "stop"}:
            return HTMLResponse("Not found", status_code=404)
        request_id = str(uuid.uuid4())
        source_ip = request.client.host if request.client else "unknown"
        accepted = AuditEvent(
            f"vm.{action}.accepted",
            "accepted",
            request_id,
            current.username,
            current.role,
            source_ip,
            instance_id,
        )
        audit_logger.emit(accepted)
        try:
            getattr(ec2_service, action)(instance_id)
        except VmError:
            audit_logger.emit(
                AuditEvent(
                    f"vm.{action}.failed",
                    "failed",
                    request_id,
                    current.username,
                    current.role,
                    source_ip,
                    instance_id,
                )
            )
            return render(
                request,
                "error.html",
                {"message": "無法完成操作，請重新整理後再試。"},
                409,
            )
        audit_logger.emit(
            AuditEvent(
                f"vm.{action}.succeeded",
                "succeeded",
                request_id,
                current.username,
                current.role,
                source_ip,
                instance_id,
            )
        )
        return RedirectResponse("/", status_code=303)

    return app


def create_app_from_env() -> FastAPI:
    return create_app(Settings.from_env(os.environ))
