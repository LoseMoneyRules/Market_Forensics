from mfapp import create_app

# Phusion Passenger / cPanel expects a WSGI callable. Namecheap's
# shared-hosting guidance recommends exposing it as `application`.
application = create_app()

# Keep `app` as an alias so local development and existing tooling continue
# to work exactly as before.
app = application

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
