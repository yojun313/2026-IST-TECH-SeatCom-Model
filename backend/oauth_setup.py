"""One-time interactive Gmail read-only OAuth authorization; run manually.

Reads configuration from backend/.env. Never commit generated token.json.
"""
from .config import get_settings


def main():
    from kakao_analysis.get_gmail_text_api.txt_from_gmail import TxtFromGmail
    settings=get_settings()
    collector=TxtFromGmail(email=settings.recipient_email,directory=settings.mail_directory)
    print('Gmail 인증 완료:',collector.email)
    print('OAuth 토큰 저장 위치:',collector.token_path)

if __name__=='__main__':main()
