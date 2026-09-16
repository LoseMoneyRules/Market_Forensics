from __future__ import annotations

import sys
from pathlib import Path

# 0.0.4 ships the heavy V3.1.12 hosted-engine dependencies alongside the app so
# Namecheap does not require a manual cPanel pip-install after each release.
_vendor = Path(__file__).resolve().parent / "_vendor"
if _vendor.is_dir() and str(_vendor) not in sys.path:
    sys.path.insert(0, str(_vendor))

from mfapp import create_app

# Phusion Passenger / cPanel expects a WSGI callable. Namecheap's shared-hosting
# guidance recommends exposing it as `application`.
application = create_app()

# Keep `app` as an alias so local development and existing tooling continue to work.
app = application

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
