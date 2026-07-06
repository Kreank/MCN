"""Service-Schicht: fachliche Schreibvorgänge über db_core.db_context.

Views (ninja-Endpoints) bleiben dünn und rufen diese Funktionen; dieselben
Funktionen nutzt später der KI-Agent. Kein Schreibweg führt an
business_transaction (SET LOCAL app.current_user_id) vorbei.
"""
