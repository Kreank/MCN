from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models.functions import Lower


class User(AbstractUser):
    """Login-Konto (Djangos Welt, Schema public, managed = True).

    Die fachliche Identität ist security.app_user — bewusst ohne Credentials
    (\"minimales Referenzziel\", Migration 0002). Die DB-Trigger protokollieren
    app.current_user_id = app_user.id; app_user_id verbindet beide Welten.
    Ein Konto ohne app_user_id kann sich einloggen, aber keine fachlichen
    Schreibvorgänge ausführen (db_core.db_context verlangt die UUID).

    Angemeldet wird sich mit der E-Mail-Adresse (accounts.backends.EmailBackend);
    `username` bleibt nur als technisches Pflichtfeld von AbstractUser bestehen.
    """

    app_user_id = models.UUIDField(null=True, blank=True, unique=True)
    email = models.EmailField("E-Mail-Adresse", blank=False)

    class Meta(AbstractUser.Meta):
        constraints = [
            # Case-insensitiv eindeutig: „Sascha@…" und „sascha@…" sind dieselbe
            # Person. Ein reines unique=True würde beide Schreibweisen zulassen
            # und den Login mehrdeutig machen.
            models.UniqueConstraint(Lower("email"), name="uniq_user_email_ci"),
        ]

    def save(self, *args, **kwargs):
        self.email = self.__class__.objects.normalize_email(self.email or "").strip()
        return super().save(*args, **kwargs)
