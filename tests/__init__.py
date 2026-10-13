"""Tests des eigenständigen Autonomy-Recovery-Versuchs."""

import os

# Kein Modellzugang aus .env: Tests rufen nie ein echtes Sprachmodell auf.
os.environ["AUTONOMY_RECOVERY_DOTENV"] = "0"
for _name in ("AUTONOMY_RECOVERY_API_KEY", "CHATAI_API_KEY", "SAIA_API_KEY"):
    os.environ.pop(_name, None)
