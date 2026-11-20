"""Make the key pair for weather-alert notifications (run once, keep the private key secret).

    python scripts/make_vapid_keys.py

Put both lines in your .env (local) or App Service app settings (Azure).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alerts import make_vapid_keys  # noqa: E402

public, private = make_vapid_keys()
print(f"VAPID_PUBLIC_KEY={public}")
print(f"VAPID_PRIVATE_KEY={private}")
