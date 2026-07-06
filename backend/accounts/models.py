from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Login-Konto (Djangos Welt, Schema public, managed = True).

    Die fachliche Identität ist security.app_user — bewusst ohne Credentials
    (\"minimales Referenzziel\", Migration 0002). Die DB-Trigger protokollieren
    app.current_user_id = app_user.id; app_user_id verbindet beide Welten.
    Ein Konto ohne app_user_id kann sich einloggen, aber keine fachlichen
    Schreibvorgänge ausführen (db_core.db_context verlangt die UUID).
    """

    app_user_id = models.UUIDField(null=True, blank=True, unique=True)
