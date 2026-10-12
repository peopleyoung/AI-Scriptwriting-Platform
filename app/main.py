from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Base, build_engine, build_session_factory, get_db
from app.models import AuditLog, SessionRecord, User
from app.schemas import (
    AuditResponse,
    BootstrapRequest,
    ChangePasswordRequest,
    CreateUserRequest,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    ResetPasswordRequest,
    SetActiveRequest,
    UserResponse,
)
from app.security import hash_password, new_session_token, session_expiry, session_token_hash, verify_password


_failed_logins: dict[tuple[str, str], list[datetime]] = {}


def _audit(db: Session, event_type: str, actor: User | None = None, target: User | None = None, metadata: dict | None = None) -> None:
    db.add(
        AuditLog(
            event_type=event_type,
            actor_user_id=actor.id if actor else None,
            target_user_id=target.id if target else None,
            metadata_json=metadata or {},
        )
    )


def _public_user(user: User) -> UserResponse:
    return UserResponse.model_validate(user)


def _error(code: str, detail: str, http_status: int) -> HTTPException:
    return HTTPException(status_code=http_status, detail={"code": code, "message": detail})


def _find_user(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def _get_user_by_id(db: Session, user_id: str) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise _error("USER_NOT_FOUND", "user not found", status.HTTP_404_NOT_FOUND)
    return user


def _cookie_options(settings: Settings) -> dict:
    return {
        "key": settings.session_cookie_name,
        "httponly": True,
        "secure": settings.cookie_secure,
        "samesite": settings.cookie_samesite,
        "max_age": settings.session_ttl_seconds,
        "path": "/",
    }


def _check_login_rate_limit(request: Request, username: str, settings: Settings) -> None:
    now = datetime.now(timezone.utc)
    key = (request.client.host if request.client else "unknown", username.lower())
    attempts = [stamp for stamp in _failed_logins.get(key, []) if (now - stamp).total_seconds() < settings.login_window_seconds]
    _failed_logins[key] = attempts
    if len(attempts) >= settings.login_max_attempts:
        raise _error("LOGIN_RATE_LIMITED", "too many failed login attempts", status.HTTP_429_TOO_MANY_REQUESTS)


def _record_failed_login(request: Request, username: str) -> None:
    key = (request.client.host if request.client else "unknown", username.lower())
    _failed_logins.setdefault(key, []).append(datetime.now(timezone.utc))


def _clear_failed_logins(request: Request, username: str) -> None:
    key = (request.client.host if request.client else "unknown", username.lower())
    _failed_logins.pop(key, None)


def _check_csrf(request: Request) -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    session_cookie = request.cookies.get(request.app.state.settings.session_cookie_name)
    if not session_cookie:
        return
    origin = request.headers.get("origin")
    expected_origin = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    if origin and origin.rstrip("/") != expected_origin.rstrip("/"):
        raise _error("CSRF_ORIGIN_REJECTED", "request origin is not allowed", status.HTTP_403_FORBIDDEN)
    csrf_cookie = request.cookies.get(request.app.state.settings.csrf_cookie_name)
    csrf_header = request.headers.get("x-csrf-token")
    if not csrf_cookie or csrf_cookie != csrf_header:
        raise _error("CSRF_TOKEN_REQUIRED", "a matching CSRF token is required", status.HTTP_403_FORBIDDEN)


def _load_session(request: Request, db: Session) -> tuple[SessionRecord, User] | None:
    token = request.cookies.get(request.app.state.settings.session_cookie_name)
    if not token:
        return None
    record = db.scalar(select(SessionRecord).where(SessionRecord.token_hash == session_token_hash(token)))
    if record is None or record.revoked_at is not None:
        return None
    now = datetime.now(timezone.utc)
    expires = record.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        record.revoked_at = now
        db.commit()
        return None
    user = db.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    return record, user


def current_user(request: Request, db: Annotated[Session, Depends(get_db)]) -> User:
    loaded = _load_session(request, db)
    if loaded is None:
        raise _error("UNAUTHENTICATED", "authentication required", status.HTTP_401_UNAUTHORIZED)
    return loaded[1]


def active_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.must_change_password:
        raise _error("PASSWORD_CHANGE_REQUIRED", "change the initial password before continuing", status.HTTP_403_FORBIDDEN)
    return user


def admin_user(request: Request, user: Annotated[User, Depends(active_user)]) -> User:
    _check_csrf(request)
    if user.role != "ADMIN":
        raise _error("FORBIDDEN", "administrator role required", status.HTTP_403_FORBIDDEN)
    return user


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    if settings.auto_create_schema and settings.environment != "production":
        Base.metadata.create_all(bind=app.state.engine)
    if settings.bootstrap_admin_username and settings.bootstrap_admin_password:
        with app.state.session_factory() as db:
            if db.scalar(select(User).where(User.role == "ADMIN")) is None:
                admin = User(
                    username=settings.bootstrap_admin_username,
                    password_hash=hash_password(settings.bootstrap_admin_password),
                    role="ADMIN",
                    must_change_password=True,
                )
                db.add(admin)
                db.flush()
                _audit(db, "ADMIN_BOOTSTRAPPED", target=admin)
                db.commit()
    yield
    app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    engine = build_engine(app_settings.database_url)
    factory = build_session_factory(engine)
    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.settings = app_settings

    @app.get("/healthz", response_model=HealthResponse)
    def healthz(db: Annotated[Session, Depends(get_db)]) -> HealthResponse:
        db.execute(text("SELECT 1"))
        return HealthResponse(status="ok")

    @app.post("/api/v1/auth/login", response_model=LoginResponse)
    @app.post("/api/v1/login", response_model=LoginResponse, include_in_schema=False)
    def login(payload: LoginRequest, request: Request, response: Response, db: Annotated[Session, Depends(get_db)]) -> LoginResponse:
        _check_login_rate_limit(request, payload.username, app_settings)
        user = _find_user(db, payload.username)
        if user is None or not verify_password(user.password_hash, payload.password):
            _record_failed_login(request, payload.username)
            raise _error("INVALID_CREDENTIALS", "invalid username or password", status.HTTP_401_UNAUTHORIZED)
        if not user.is_active:
            raise _error("ACCOUNT_DISABLED", "account is disabled", status.HTTP_403_FORBIDDEN)
        _clear_failed_logins(request, payload.username)
        token = new_session_token()
        db.add(SessionRecord(user_id=user.id, token_hash=session_token_hash(token), expires_at=session_expiry(app_settings.session_ttl_seconds)))
        _audit(db, "LOGIN_SUCCEEDED", target=user)
        db.commit()
        response.set_cookie(value=token, **_cookie_options(app_settings))
        response.set_cookie(
            key=app_settings.csrf_cookie_name,
            value=new_session_token(),
            httponly=False,
            secure=app_settings.cookie_secure,
            samesite="strict",
            max_age=app_settings.session_ttl_seconds,
            path="/",
        )
        return LoginResponse(user=_public_user(user), must_change_password=user.must_change_password)

    @app.post("/api/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    @app.post("/api/v1/logout", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
    def logout(request: Request, response: Response, db: Annotated[Session, Depends(get_db)]) -> Response:
        _check_csrf(request)
        loaded = _load_session(request, db)
        if loaded:
            session, user = loaded
            session.revoked_at = datetime.now(timezone.utc)
            _audit(db, "LOGOUT", actor=user)
            db.commit()
        response.delete_cookie(app_settings.session_cookie_name, path="/")
        response.delete_cookie(app_settings.csrf_cookie_name, path="/")
        return response

    @app.get("/api/v1/auth/me", response_model=UserResponse)
    def me(user: Annotated[User, Depends(active_user)]) -> UserResponse:
        return _public_user(user)

    @app.post("/api/v1/auth/change-password", response_model=UserResponse)
    @app.post("/api/v1/change-password", response_model=UserResponse, include_in_schema=False)
    def change_password(payload: ChangePasswordRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        _check_csrf(request)
        user = current_user(request, db)
        if not verify_password(user.password_hash, payload.current_password):
            raise _error("INVALID_PASSWORD", "current password is invalid", status.HTTP_400_BAD_REQUEST)
        user.password_hash = hash_password(payload.new_password)
        user.must_change_password = False
        _audit(db, "PASSWORD_CHANGED", actor=user, target=user)
        db.commit()
        return _public_user(user)

    @app.post("/api/v1/admin/bootstrap", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
    @app.post("/api/v1/setup/bootstrap", response_model=UserResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
    def bootstrap(payload: BootstrapRequest, request: Request, db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        # Bootstrap is one-time, and production deployments can additionally require a setup secret.
        _check_csrf(request)
        if db.scalar(select(User).where(User.role == "ADMIN")) is not None:
            raise _error("BOOTSTRAP_COMPLETE", "an administrator already exists", status.HTTP_409_CONFLICT)
        if app_settings.environment == "production" and not app_settings.bootstrap_secret:
            raise _error("BOOTSTRAP_DISABLED", "production bootstrap requires a setup secret", status.HTTP_403_FORBIDDEN)
        if app_settings.bootstrap_secret and payload.bootstrap_token != app_settings.bootstrap_secret:
            raise _error("BOOTSTRAP_TOKEN_INVALID", "setup secret is invalid", status.HTTP_403_FORBIDDEN)
        user = User(username=payload.username, password_hash=hash_password(payload.password), role="ADMIN", must_change_password=True)
        db.add(user)
        try:
            db.flush()
            _audit(db, "ADMIN_BOOTSTRAPPED", target=user)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise _error("USERNAME_TAKEN", "username is already in use", status.HTTP_409_CONFLICT)
        return _public_user(user)

    @app.post("/api/v1/admin/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
    def create_user(payload: CreateUserRequest, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        # Cookie-authenticated mutations also accept non-browser clients without an Origin header.
        user = User(username=payload.username, password_hash=hash_password(payload.password), role=payload.role, must_change_password=True)
        db.add(user)
        try:
            db.flush()
            _audit(db, "USER_CREATED", actor=actor, target=user, metadata={"role": user.role})
            db.commit()
        except IntegrityError:
            db.rollback()
            raise _error("USERNAME_TAKEN", "username is already in use", status.HTTP_409_CONFLICT)
        return _public_user(user)

    @app.get("/api/v1/admin/users", response_model=list[UserResponse])
    def list_users(_: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> list[UserResponse]:
        return [_public_user(user) for user in db.scalars(select(User).order_by(User.created_at)).all()]

    @app.patch("/api/v1/admin/users/{user_id}/status", response_model=UserResponse)
    @app.patch("/api/v1/admin/users/{user_id}", response_model=UserResponse, include_in_schema=False)
    def set_user_status(user_id: str, payload: SetActiveRequest, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        target = _get_user_by_id(db, user_id)
        if target.id == actor.id and not payload.active:
            raise _error("SELF_DISABLE_FORBIDDEN", "an administrator cannot disable their own account", status.HTTP_400_BAD_REQUEST)
        if target.role == "ADMIN" and not payload.active:
            active_admins = db.scalars(select(User.id).where(User.role == "ADMIN", User.is_active.is_(True))).all()
            if len(active_admins) <= 1:
                raise _error("LAST_ADMIN_FORBIDDEN", "the last active administrator cannot be disabled", status.HTTP_400_BAD_REQUEST)
        target.is_active = payload.active
        if not payload.active:
            now = datetime.now(timezone.utc)
            for session in target.sessions:
                session.revoked_at = now
        _audit(db, "USER_ENABLED" if payload.active else "USER_DISABLED", actor=actor, target=target)
        db.commit()
        return _public_user(target)

    @app.post("/api/v1/admin/users/{user_id}/disable", response_model=UserResponse, include_in_schema=False)
    def disable_user(user_id: str, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        return set_user_status(user_id, SetActiveRequest(active=False), actor, db)

    @app.post("/api/v1/admin/users/{user_id}/enable", response_model=UserResponse, include_in_schema=False)
    def enable_user(user_id: str, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        return set_user_status(user_id, SetActiveRequest(active=True), actor, db)

    @app.post("/api/v1/admin/users/{user_id}/reset-password", response_model=UserResponse)
    def reset_password(user_id: str, payload: ResetPasswordRequest, actor: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> UserResponse:
        target = _get_user_by_id(db, user_id)
        target.password_hash = hash_password(payload.password)
        target.must_change_password = True
        now = datetime.now(timezone.utc)
        for session in target.sessions:
            session.revoked_at = now
        _audit(db, "PASSWORD_RESET", actor=actor, target=target)
        db.commit()
        return _public_user(target)

    @app.get("/api/v1/admin/audit", response_model=list[AuditResponse])
    def audit_logs(_: Annotated[User, Depends(admin_user)], db: Annotated[Session, Depends(get_db)]) -> list[AuditResponse]:
        logs = db.scalars(select(AuditLog).order_by(AuditLog.created_at.desc())).all()
        return [AuditResponse(id=log.id, event_type=log.event_type, actor_user_id=log.actor_user_id, target_user_id=log.target_user_id, metadata=log.metadata_json or {}, created_at=log.created_at) for log in logs]

    return app


app = create_app()

