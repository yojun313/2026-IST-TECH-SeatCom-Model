import base64
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import jwt
import pytest
from fastapi.testclient import TestClient

from backend.config import get_settings
from backend.main import create_app


class FakeIdentity:
    def __init__(self,matched=True): self.matched=matched;self.calls=[]
    def verify(self, request):
        self.calls.append(request)
        return {
            'verification_request_id':request['verification_request_id'],
            'user_id':request['user_id'],
            'matched':self.matched,'verification':'approved' if self.matched else 'rejected',
            'checks':{'email_match':self.matched,'name_match':True},
            'verified_at':datetime.now(timezone.utc).isoformat(),
        }

class FakeAnalyzer:
    def __init__(self):self.calls=[]
    def analyze_by_GPT_OSS_API(self, source, name):
        assert Path(source).exists()
        self.calls.append((name,Path(source).read_text(encoding='utf-8')))
        return {'participant_id':name,'behavioral_summary':'테스트 분석','preferences':{},'evidence':[],
                'seat_recommendation':'조용한 자리','follow_up_question':''}

@pytest.fixture
def api(tmp_path):
    settings=replace(get_settings(),
        database_path=tmp_path/'db.sqlite3',attachment_dir=tmp_path/'files',mail_directory=tmp_path/'mail',
        auth_jwt_algorithm='HS256',auth_jwt_secret='test-only-high-entropy-secret-at-least-32-bytes-long',
        auth_issuer='test-issuer',auth_audience='test-audience',
        internal_service_token='test-internal-token',worker_enabled=False,gmail_enabled=False)
    identity=FakeIdentity(); analyzer=FakeAnalyzer()
    app=create_app(settings=settings,identity_client=identity,analyzer=analyzer)
    c=TestClient(app)
    def auth(uid):
        from datetime import timedelta
        now=datetime.now(timezone.utc)
        t=jwt.encode({'sub':uid,'iss':'test-issuer','aud':'test-audience','iat':now,'exp':now+timedelta(hours=1)},
                     'test-only-high-entropy-secret-at-least-32-bytes-long',algorithm='HS256')
        return {'Authorization':'Bearer '+t}
    return c,app,auth,identity,analyzer


def persona_ready(api, uid='a'):
    c,app,auth,_,_=api
    h=auth(uid)
    for k,v in [('outlet','required'),('hvac_proximity','near'),('noise_sensitivity','high'),('partition','yes')]:
        r=c.put(f'/api/v1/persona/answers/{k}',json={'answer':v},headers=h)
        assert r.status_code==200,r.text
    assert c.post('/api/v1/persona/analyze',json={'analysis_type':'library_preference'},headers=h).status_code==200


def create_session(api,uid='a'):
    c,app,auth,_,_=api
    r=c.post('/api/v1/lab/sessions',json={'target_name':'홍길동','consent':True},headers=auth(uid))
    assert r.status_code==201,r.text
    return r.json()


def stage_event(api, s, *, sender='sender@example.com', msg='message-A', fid='part-A'):
    c,app,auth,ident,analyzer=api
    h={'Authorization':'Bearer test-internal-token'}
    attachment=c.post('/internal/v1/lab/attachments',headers=h,json={
        'message_id':msg,'attachment_id':fid,'filename':'KakaoTalk.txt',
        'content_base64':base64.b64encode('2026년 11월 15일 홍길동 : 노트북 켰어요'.encode()).decode(),
    })
    assert attachment.status_code==200,attachment.text
    body={'event_id':'event-'+msg,'upload_id':s['upload_id'],'upload_code':s['upload_code'],
          'sender':{'name':'홍길동','email':sender},
          'email':{'subject':s['mail_subject'],'message_id':msg,'received_at':datetime.now(timezone.utc).isoformat()},
          'attachment':{'file_id':attachment.json()['file_id'],'filename':'KakaoTalk.txt','file_type':'txt','validation_status':'valid'}}
    r=c.post('/internal/v1/lab/email-received',json=body,headers=h)
    return r,body


def test_questions_and_persona(api):
    c,app,auth,_,_=api
    assert c.get('/api/v1/persona/questions').status_code==401
    q=c.get('/api/v1/persona/questions',headers=auth('a'))
    assert q.status_code==200 and len(q.json()['questions'])==4
    assert [x['id'] for x in q.json()['questions']]==['outlet','hvac_proximity','noise_sensitivity','partition']
    bad=c.put('/api/v1/persona/answers/outlet',json={'answer':'invalid'},headers=auth('a'))
    assert bad.status_code==422 and bad.json()['error']['code']=='INVALID_ANSWER'
    inc=c.post('/api/v1/persona/analyze',json={'analysis_type':'library_preference'},headers=auth('a'))
    assert inc.status_code==422 and len(inc.json()['error']['missing_questions'])==4
    persona_ready(api)
    result=c.get('/api/v1/persona/result',headers=auth('a'))
    assert result.status_code==200
    assert result.json()['seat_preferences']['partition']=='yes'
    assert c.get('/api/v1/persona/result',headers=auth('b')).status_code==404
    # Updating a single answer invalidates previous saved result.
    c.put('/api/v1/persona/answers/partition',json={'answer':'no'},headers=auth('a'))
    assert c.get('/api/v1/persona/result',headers=auth('a')).status_code==404
    assert c.get('/api/v1/persona/answers',headers=auth('a')).json()['answered_count']==4


def test_lab_approval_and_result(api):
    c,app,auth,identity,analyzer=api
    assert c.post('/api/v1/lab/sessions',headers=auth('a'),json={'target_name':'홍길동','consent':True}).status_code==404
    persona_ready(api)
    assert c.post('/api/v1/lab/sessions',headers=auth('a'),json={'target_name':'홍길동','consent':False}).status_code==400
    s=create_session(api)
    assert c.get(f'/api/v1/lab/sessions/{s["upload_id"]}/status',headers=auth('b')).status_code==403
    assert c.post('/internal/v1/lab/attachments',json={}).status_code==401
    assert c.post('/internal/v1/lab/email-received',json={}).status_code==401
    received,event=stage_event(api,s)
    assert received.status_code==200,received.text
    assert received.json()['status']=='verifying_identity'
    duplicated=c.post('/internal/v1/lab/email-received',json=event,headers={'Authorization':'Bearer test-internal-token'})
    assert duplicated.status_code==200
    assert len(analyzer.calls)==0
    app.state.service.verify_one(s['upload_id'])
    verified=c.get(f'/api/v1/lab/sessions/{s["upload_id"]}/status',headers=auth('a')).json()
    assert verified['status']=='verified' and verified['identity']['result']=='OK'
    with app.state.service.store.db() as db:
        row=dict(db.execute('SELECT * FROM lab_sessions WHERE upload_id=?',(s['upload_id'],)).fetchone())
    approve=c.post('/internal/v1/lab/analysis-approved',headers={'Authorization':'Bearer test-internal-token'},json={
        'upload_id':s['upload_id'],'user_id':'a','identity_verification':{
            'matched':True,'verification':'approved','verification_request_id':row['verification_request_id']},
        'analysis':{'analysis_type':'kakao_chat','file_id':row['file_id'],'target_name':'홍길동'}})
    assert approve.status_code==202,approve.text
    app.state.service.run_analysis(s['upload_id'])
    status=c.get(f'/api/v1/lab/sessions/{s["upload_id"]}/status',headers=auth('a')).json()
    assert status['status']=='completed'
    aid=status['analysis']['analysis_id']
    result=c.get(f'/api/v1/lab/analyses/{aid}',headers=auth('a'))
    assert result.status_code==200
    assert result.json()['analysis']['participant_id']=='홍길동'
    assert len(analyzer.calls)==1
    assert c.get(f'/api/v1/lab/analyses/{aid}',headers=auth('b')).status_code==403
    assert c.delete(f'/api/v1/lab/sessions/{s["upload_id"]}',headers=auth('a')).status_code==200
    assert c.get(f'/api/v1/lab/analyses/{aid}',headers=auth('a')).status_code==404


def test_lab_rejection_and_invalid_email(api):
    c,app,auth,identity,analyzer=api
    persona_ready(api)
    s=create_session(api)
    received,event=stage_event(api,s,msg='reject1')
    assert received.status_code==200
    identity.matched=False
    app.state.service.run_once()
    status=c.get(f'/api/v1/lab/sessions/{s["upload_id"]}/status',headers=auth('a')).json()
    assert status['status']=='rejected' and status['identity']['result']=='NOT_MATCHED'
    assert not analyzer.calls
    s2=create_session(api)
    h={'Authorization':'Bearer test-internal-token'}
    # Original attachment cannot be rebound to another user's session
    copied=dict(event);copied.update({'event_id':'event-other','upload_id':s2['upload_id'],'upload_code':s2['upload_code']})
    copied['email']=dict(event['email'],subject=s2['mail_subject'])
    assert c.post('/internal/v1/lab/email-received',json=copied,headers=h).status_code==409


def test_input_and_token_guard(api):
    c,app,auth,identity,analyzer=api
    assert c.get('/api/v1/persona/questions',headers={'Authorization':'Bearer faked'}).status_code==401
    assert c.put('/api/v1/persona/answers/bad-id',json={'answer':'yes'},headers=auth('a')).status_code==404
    assert c.put('/api/v1/persona/answers/partition',json={'answer':'yes','hidden':'other'},headers=auth('a')).status_code==422
    r=c.post('/internal/v1/lab/attachments',headers={'Authorization':'Bearer test-internal-token'},json={
        'message_id':'m','attachment_id':'a','filename':'secret.txt','content_base64':base64.b64encode(b'a\x00b').decode()})
    assert r.status_code==422
