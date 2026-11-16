"""Server-only Firestore Admin SDK initialization."""

from __future__ import annotations

import firebase_admin
from firebase_admin import credentials, firestore

from app_api.settings import AppSettings

_APP_NAME = "finsolve-app-api"


def create_firestore_client(settings: AppSettings) -> firestore.Client:
    settings.validate()
    credential_path = settings.firebase_service_account_path
    if not credential_path.is_file():
        raise RuntimeError("The configured Firebase service-account file is unavailable.")

    try:
        app = firebase_admin.get_app(_APP_NAME)
    except ValueError:
        app = firebase_admin.initialize_app(
            credentials.Certificate(str(credential_path)),
            {"projectId": settings.firebase_project_id},
            name=_APP_NAME,
        )
    if app.project_id != settings.firebase_project_id:
        raise RuntimeError("The initialized Firebase app does not match the configured project.")
    return firestore.client(app=app)
