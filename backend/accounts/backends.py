"""Authentifizierung über die E-Mail-Adresse statt über den Benutzernamen.

Jeder Mitarbeiter hat eine Firmen-Mailadresse; ein zusätzlicher Benutzername
wäre eine zweite Kennung ohne Nutzen. `username` bleibt als technisches Feld
bestehen (AbstractUser verlangt es), spielt für die Anmeldung aber keine Rolle.

Vergleich case-insensitiv: „Sascha@…" und „sascha@…" sind dieselbe Person. Der
UniqueConstraint auf Lower('email') stellt sicher, dass die Suche höchstens eine
Zeile trifft.
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend


class EmailBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        email = kwargs.get("email") or username
        if not email or not password:
            return None

        try:
            user = User.objects.get(email__iexact=email.strip())
        except User.DoesNotExist:
            # Passwort-Hash trotzdem einmal durchrechnen: sonst verrät die
            # Antwortzeit, ob die Adresse existiert (User-Enumeration).
            User().set_password(password)
            return None
        except User.MultipleObjectsReturned:
            # Kann der UniqueConstraint nicht auftreten lassen; falls doch,
            # lieber niemanden einloggen als den falschen.
            return None

        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
