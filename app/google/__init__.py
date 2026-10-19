"""Google integration (T7): OAuth (FR-2), the encrypted credential store, and a thin
Drive v3 client used by `app.sources.drive.DriveSource` and the folder endpoints (FR-3).

Nothing here keeps tokens between requests/iterations (PRD §2, FR-2): credentials are
read from Postgres and decrypted for one request or one worker iteration.
"""
