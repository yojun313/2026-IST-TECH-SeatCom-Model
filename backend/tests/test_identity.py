"""실험실 본인 확인(백엔드 내장): 메일 보낸 주소 = 가입 때 인증한 이메일."""
from backend.auth import LocalIdentityVerifier, AuthService, hash_password
from backend.store import Store, now_iso


def test_local_identity(tmp_path):
    store = Store(tmp_path / 'db.sqlite3')

    class S:  # AuthService가 테이블을 만든다
        auth_jwt_secret = 'x' * 32
    AuthService(S(), store)
    with store.db() as db:
        db.execute('INSERT INTO auth_users VALUES(?,?,?,?,?,?)', ('usr_1', 'me@example.com', '홍길동', '홍길동', hash_password('pw1234'), now_iso()))
    v = LocalIdentityVerifier(store)
    req = {'verification_request_id': 'ver-1', 'user_id': 'usr_1', 'upload_id': 'u', 'sender_email': 'ME@example.com ', 'target_name': '길동'}
    ok = v.verify(req)
    assert ok['matched'] is True and ok['checks'] == {'email_match': True, 'name_match': True}
    bad = v.verify({**req, 'sender_email': 'other@example.com'})
    assert bad['matched'] is False and bad['reason_code'] == 'EMAIL_MISMATCH'
    guest = v.verify({**req, 'user_id': 'guest-abc'})
    assert guest['matched'] is False
