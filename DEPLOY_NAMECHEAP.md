# Deploy Market Forensics v0.0.1 on Namecheap Stellar Plus

This build is designed for Namecheap Shared Hosting using **Setup Python App / WSGI**.

## 1. Choose the address
Create a subdomain in cPanel such as `app.yourdomain.com` or use the main domain if Market Forensics will live there. Enable SSL/HTTPS before inviting anyone.

## 2. Create MariaDB database
In cPanel create a MySQL/MariaDB database and database user, then grant that user ALL PRIVILEGES on the Market Forensics database. Keep the database name, username, password and host (`localhost` on most cPanel shared-host setups).

## 3. Upload application
Upload the repository files into an application folder, for example `/home/CPANEL_USER/market_forensics`.

Do not upload `.env`, local databases, generated 2FA QR images, API secrets or personal data.

## 4. Create Python application
In cPanel open **Setup Python App** and create an application:

- Python: 3.13 (or the latest supported 3.13 build offered)
- Application root: `market_forensics`
- Application URL: your chosen domain/subdomain
- Startup file: `app.py`
- Entry point: `app`

## 5. Install dependencies
Inside Setup Python App add `requirements.txt` under configuration files and run **Pip Install**, or activate the virtual environment shown by cPanel and run `pip install -r requirements.txt`.

## 6. Generate production secrets
Activate the app virtual environment and run `python manage.py generate-secrets`.

Copy the generated values. Never commit them to GitHub.

## 7. Configure environment variables
In Setup Python App add:

- `MF_ENV=production`
- `MF_SECRET_KEY=<generated value>`
- `MF_ENCRYPTION_KEY=<generated Fernet key>`
- `MF_DATABASE_URL=mysql+pymysql://DBUSER:URL_ENCODED_PASSWORD@localhost/DBNAME`
- `MF_SITE_NAME=Market Forensics`
- `MF_SESSION_DAYS=7`

If the database password contains characters such as `@`, `:`, `/`, `#` or `%`, URL-encode the password before placing it in the connection URL.

## 8. Create CONTROL account
From the activated Python environment run:

`python manage.py bootstrap-admin --email YOUR_EMAIL --name "Your Name"`

Choose a long unique password. The command creates `instance/control-2fa.png`. Open it, scan it with your authenticator, confirm the account works, then delete the PNG from the server.

Optional test data: `python manage.py seed-demo`

The demo uses fictional ticker `MFCO` and is not real investment research.

## 9. Restart application
Return to **Setup Python App** and press **Restart**.

Open `https://YOUR_DOMAIN/health`.

Expected response: `{"status":"ok","version":"0.0.1"}`

Then open the home page and sign in with CONTROL + authenticator code.

## 10. First beta invite
Inside CONTROL create one FRIEND invite for yourself or a trusted test email. Open the invite link on a phone, complete password + 2FA enrollment, and verify the mobile experience before inviting anyone else.

## Pre-invite checklist
- HTTPS works with no certificate warning
- CONTROL requires password + 2FA
- FRIEND cannot open `/control`
- INSIDER cannot open `/control`
- logout works
- invite link expires/works only once
- `instance/` and local databases are not in GitHub
- production secrets are only in cPanel environment variables
- backup is enabled
- phone, iPad and desktop layouts are tested

## GitHub deployment note
Namecheap Shared Hosting does not offer a native Git VCS deployment workflow on every shared plan. For v0.0.1, upload the repository files to the Python application root first. Automated deployment from GitHub can be added later over SSH if the hosting account permits it.
