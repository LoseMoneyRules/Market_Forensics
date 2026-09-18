from __future__ import annotations

from pathlib import Path
import sys

# Deploys carry the report renderer with the application so Namecheap does not
# depend on a manual cPanel pip step. Local/dev environments simply ignore it.
_reporting_vendor = Path(__file__).resolve().parent / "mfapp" / "_reporting_vendor"
if _reporting_vendor.is_dir():
    sys.path.insert(0, str(_reporting_vendor))

from mfapp import create_app

# Phusion Passenger / cPanel WSGI callable.
application = create_app()
app = application

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
