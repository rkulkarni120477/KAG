"""Load local configuration before database and client modules import."""
import os
from pathlib import Path

from dotenv import load_dotenv

if os.getenv("KAG_LOAD_DOTENV", "true").lower() == "true":
    load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)
