# Legacy conversion — 0.0.4 to 0.1.0

Migration key: `legacy_0_0_4_to_0_1_0`.

At 0.1.0 WSGI startup the schema bootstrap creates missing `mf_*` tables and, once only, converts useful legacy research data when legacy tables are present. Existing user IDs, password hashes, TOTP secrets and encrypted provider credentials are not rewritten.

The converter maps legacy companies/tickers, research workspace content, market snapshots, financial periods, positions, monitoring records, decision journal entries and publications into the new model. It then records the migration key so the two research systems are never synchronized continuously.

If startup migration fails, the WSGI process does not become healthy and deployment health check fails closed.
