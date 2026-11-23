"""Live-play server package.

``python -m server`` starts uvicorn on 127.0.0.1:8000, serving the JSON API under
``/api``, the interactive docs at ``/docs``, and the replay viewer from ``web/`` at
``/``. The package is deliberately thin: rules, search, and the game loop live in
``engine/``, ``bots/``, and ``harness/``.
"""
