from __future__ import annotations

from mfapp import create_app

# Phusion Passenger / cPanel WSGI callable.
application = create_app()
app = application

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
