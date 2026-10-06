from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import uvicorn

from survey.router import router as survey_router
from shopsurvey.router import router as shop_survey_router
from aiissuemap.router import router as aiissuemap_router
from cctv.router import router as cctv_router
from notification.router import router as notification_router
from chatbot.router import router as chatbot_router
from shopmap.router import router as shopmap_router

# FastAPI 앱 생성
app = FastAPI(
    title="Allimio AI API", description="Allimio AI 분석 API", version="1.0.0"
)


# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================
# Router 등록
# ==============================

# 설문조사 AI
app.include_router(survey_router)

# AI 이슈 도면
app.include_router(aiissuemap_router)

# CCTV 이상행동 AI (Jetson 워커 -> 서버)
app.include_router(cctv_router)

# 알림 AI
app.include_router(notification_router)

# 챗봇 상담 AI (RAG 검색) / 상담내용 요약 AI
app.include_router(chatbot_router)

# 매장 원본 도면 저장
app.include_router(shopmap_router)

# 매장 고객 설문 AI (요약 + 긍정/부정 점수)
app.include_router(shop_survey_router)


# ==============================
# 서버 확인
# ==============================


@app.get("/")
def root():
    return {"message": "Allimio AI Server", "status": "running"}


# ==============================
# FastAPI 실행
# ==============================

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=11200, reload=True)
