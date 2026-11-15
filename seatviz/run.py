import logging
import os

import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    # 기본은 로컬 전용(127.0.0.1). 외부 공개는 리버스 프록시 뒤에서 HOST 로 조정.
    host = os.getenv("HOST", "127.0.0.1")
    log_level = os.getenv("LOG_LEVEL", "info").lower()
    logging.basicConfig(level=log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        port = int(os.getenv("PORT", "8026"))
    except ValueError:
        port = 8026
    uvicorn.run("app.main:app", host=host, port=port, log_level=log_level, server_header=False, access_log=True)
