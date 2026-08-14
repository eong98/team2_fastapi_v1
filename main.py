from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import uvicorn

from survey.router import router as survey_router
from survey.router import router as survey_router
from shopmap.router import router as shopmap_router


# FastAPI 앱 생성
app = FastAPI(
    title="Allimio AI API",
    description="Allimio AI 분석 API",
    version="1.0.0"
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

# 매장 도면 AI
app.include_router(shopmap_router)


# ==============================
# 서버 확인
# ==============================

@app.get("/")
def root():
    return {
        "message": "Allimio AI Server",
        "status": "running"
    }


# ==============================
# FastAPI 실행
# ==============================

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=11200,
        reload=True
    )