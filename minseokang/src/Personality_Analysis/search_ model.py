
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

env_path = (
    Path.home()
    / "2026-IST-TECH-SeatCom-Model"
    / "minseokang"
    / "src"
    / "Personality_Analysis"
    / ".env"
)

load_dotenv(env_path, override=True)

client = OpenAI(
    api_key=os.environ["OSS_API_KEY"],
    base_url=os.environ["OSS_BASE_URL"],
)

models = client.models.list()

for model in models.data:
    print(model.id)
