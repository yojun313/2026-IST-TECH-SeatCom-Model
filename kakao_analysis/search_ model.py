import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

env_path = Path(__file__).resolve().parent / ".env"

load_dotenv(env_path, override=True)

client = OpenAI(
    api_key=os.environ["OSS_API_KEY"],
    base_url=os.environ["OSS_BASE_URL"],
)

models = client.models.list()

for model in models.data:
    print(model.id)
