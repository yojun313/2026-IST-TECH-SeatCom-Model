"""FastAPI SeatCom API. Run: uvicorn backend.main:app --reload"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .models import AnswerIn, AnalyzeIn, SessionIn, AttachmentIn, EmailEventIn, ApprovalIn, SeatRecommendationIn, RecommendationEventIn, EmailCodeIn, SignupIn, LoginIn, PasswordResetIn, AccountDeleteIn, RoomRecommendationIn, PreferencesIn
from .auth import AuthService
from .recommend import RecommendationService
from .questions import QUESTION_DEFS
from .security import claims_from_token, require_internal
from .service import SeatComService


def create_app(*, settings=None, analyzer=None, identity_client=None):
    settings=settings or get_settings()
    service=SeatComService(settings, analyzer=analyzer, identity_client=identity_client)
    reco=RecommendationService(settings, service.store)
    auth=AuthService(settings, service.store)

    @asynccontextmanager
    async def lifespan(app):
        reco.load()
        reco.preload_rooms()
        service.start()
        try:yield
        finally:service.stop()

    app=FastAPI(title='SeatCom Backend',version='2.1.0',lifespan=lifespan,
                description='회원가입(이메일 인증)·페르소나 4문항·카카오톡 실험실·좌석 추천 API')
    app.state.service=service
    app.state.reco=reco
    app.state.auth=auth
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware,allow_origins=list(settings.cors_origins),
            allow_methods=['GET','PUT','POST','DELETE'],allow_headers=['Authorization','Content-Type'])

    def user(authorization: Annotated[str|None, Header()] = None) -> str:
        return auth.check_token(claims_from_token(authorization,settings))

    def internal(authorization: Annotated[str|None, Header()] = None):
        require_internal(authorization,settings)

    @app.exception_handler(HTTPException)
    async def http_error(request,exc):
        if isinstance(exc.detail,dict) and 'code' in exc.detail:
            return JSONResponse(status_code=exc.status_code,content={'error':exc.detail})
        return JSONResponse(status_code=exc.status_code,content={'error':{'code':'HTTP_ERROR','message':str(exc.detail)}})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request,exc):
        # Do not expose request bodies: they may contain uploaded conversations.
        return JSONResponse(status_code=422,content={'error':{'code':'VALIDATION_ERROR','message':'요청 형식 또는 값이 올바르지 않습니다.'}})

    @app.get('/health')
    def health():return {'status':'ok','service':'SeatCom','version':'2.1'}

    # ---------- 회원가입·로그인 (이메일 인증) ----------
    @app.post('/api/v1/auth/email-codes',tags=['Auth'])
    def send_email_code(body: EmailCodeIn):
        return auth.send_code(body.email,body.purpose)

    @app.post('/api/v1/auth/signup',status_code=201,tags=['Auth'])
    def signup(body: SignupIn):
        return auth.signup(body.email,body.code,body.nickname,body.password)

    @app.post('/api/v1/auth/login',tags=['Auth'])
    def login(body: LoginIn):
        return auth.login(body.nickname,body.password)

    @app.get('/api/v1/auth/me',tags=['Auth'])
    def me(uid: str=Depends(user)):
        return auth.me(uid)

    @app.post('/api/v1/auth/password-reset',tags=['Auth'])
    def password_reset(body: PasswordResetIn):
        return auth.reset_password(body.email,body.code,body.password)

    @app.delete('/api/v1/auth/me',tags=['Auth'])
    def delete_me(body: AccountDeleteIn, uid: str=Depends(user)):
        out=auth.delete_account(uid,body.password)
        reco.forget_user(uid)
        return out

    @app.get('/api/v1/persona/questions',tags=['Persona'])
    def questions(uid: str=Depends(user)):
        return {'version':'2.1','required_count':4,'questions':QUESTION_DEFS}

    @app.put('/api/v1/persona/answers/{question_id}',tags=['Persona'])
    def save_answer(question_id: str,body: AnswerIn,uid: str=Depends(user)):
        return service.put_answer(uid,question_id,body.answer)

    @app.get('/api/v1/persona/answers',tags=['Persona'])
    def saved_answers(uid: str=Depends(user)):
        return service.persona_answers(uid)

    @app.post('/api/v1/persona/analyze',tags=['Persona'])
    def analyze_persona(body: AnalyzeIn,uid: str=Depends(user)):
        return service.analyze_persona(uid)

    @app.get('/api/v1/persona/result',tags=['Persona'])
    def persona_result(uid: str=Depends(user)):
        return service.get_persona_result(uid)

    @app.post('/api/v1/lab/sessions',status_code=201,tags=['Laboratory'])
    def create_session(body: SessionIn,uid: str=Depends(user)):
        return service.create_session(uid,body)

    @app.get('/api/v1/lab/sessions/{upload_id}/status',tags=['Laboratory'])
    def get_status(upload_id:str,uid: str=Depends(user)):
        return service.status(upload_id,uid)

    @app.get('/api/v1/lab/analyses/{analysis_id}',tags=['Laboratory'])
    def get_analysis(analysis_id:str,uid: str=Depends(user)):
        return service.analysis_result(uid,analysis_id)

    @app.delete('/api/v1/lab/sessions/{upload_id}',tags=['Laboratory'])
    def delete_session(upload_id:str,uid: str=Depends(user)):
        return service.delete_session(uid,upload_id)

    # ---------- 좌석 추천 (seat_model 학습 모델 통합) ----------
    @app.get('/api/v1/system/model',tags=['Seat recommendation'])
    def model_status():
        return reco.status()

    @app.get('/api/v1/libraries',tags=['Libraries'])
    def list_libraries(uid: str=Depends(user)):
        return reco.list_libraries()

    @app.get('/api/v1/libraries/{library_id}/rooms',tags=['Libraries'])
    def library_rooms(library_id: str,uid: str=Depends(user)):
        return reco.library_rooms(library_id)

    @app.post('/api/v1/recommendations/rooms',tags=['Seat recommendation'])
    def recommend_rooms(body: RoomRecommendationIn,uid: str=Depends(user)):
        return reco.recommend_rooms(uid,body.libraryIds,body.options.topK)

    @app.get('/api/v1/users/me/preferences',tags=['Preferences'])
    def get_preferences(uid: str=Depends(user)):
        return reco.get_preferences(uid)

    @app.put('/api/v1/users/me/preferences',tags=['Preferences'])
    def put_preferences(body: PreferencesIn,uid: str=Depends(user)):
        return reco.put_preferences(uid,body.expectedRevision,[i.model_dump() for i in body.items],body.laptopRequired)

    @app.get('/api/v1/users/me/history',tags=['Preferences'])
    def history(uid: str=Depends(user)):
        return reco.history(uid)

    @app.get('/api/v1/rooms/{room_id}/map',tags=['Seat recommendation'])
    def room_map(room_id: str,uid: str=Depends(user)):
        return reco.room_map(room_id)

    @app.get('/api/v1/rooms/{room_id}/floor',tags=['Seat recommendation'])
    def room_floor(room_id: str,uid: str=Depends(user)):
        return reco.floor_plan(room_id)

    @app.get('/api/v1/recommendations/{recommendation_id}/seats/{seat_id}',tags=['Seat recommendation'])
    def explain_seat(recommendation_id: str,seat_id: str,uid: str=Depends(user)):
        return reco.explain_seat(uid,recommendation_id,seat_id)

    @app.get('/api/v1/rooms/{room_id}/availability',tags=['Seat recommendation'])
    def room_availability(room_id: str,uid: str=Depends(user)):
        return reco.availability(room_id)

    @app.post('/api/v1/recommendations/seats',tags=['Seat recommendation'])
    def recommend_seats(body: SeatRecommendationIn,uid: str=Depends(user)):
        return reco.recommend(uid,body.roomId,body.options.topK)

    @app.post('/api/v1/recommendations/{recommendation_id}/events',tags=['Seat recommendation'])
    def recommendation_event(recommendation_id: str,body: RecommendationEventIn,uid: str=Depends(user)):
        return reco.record_event(uid,recommendation_id,body.model_dump(mode='json'))

    @app.get('/api/v1/users/me/preferences/insights',tags=['Seat recommendation'])
    def preference_insights(uid: str=Depends(user)):
        return reco.insights(uid)

    @app.post('/internal/v1/lab/attachments',tags=['Internal'])
    def store_attachment(body: AttachmentIn, _:None=Depends(internal)):
        return service.store_attachment(body)

    @app.post('/internal/v1/lab/email-received',tags=['Internal'])
    def email_received(body: EmailEventIn, _:None=Depends(internal)):
        return service.ingest_email(body)

    @app.post('/internal/v1/lab/analysis-approved',status_code=202,tags=['Internal'])
    def analysis_approved(body: ApprovalIn, tasks: BackgroundTasks, _:None=Depends(internal)):
        response=service.approve_start(body)
        # Explicit internal start works even with the optional polling worker disabled.
        if response['status'] == 'analyzing':
            tasks.add_task(service.run_analysis, body.upload_id)
        return response

    return app

app=create_app()
