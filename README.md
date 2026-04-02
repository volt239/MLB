# MLB Live Dashboard (Python)

MLB 실시간 대시보드입니다.
무료 `statsapi.mlb.com` 데이터를 사용합니다.

## 포함 기능
- FastAPI 웹 대시보드: `http://127.0.0.1:8000`
- Streamlit 대시보드: `streamlit_mlb.py`
- 점수/이닝/B-S-O/주자 베이스 상태/선수 정보 갱신
- 추적 대상: `Los Angeles Dodgers`, `San Diego Padres` (각 팀의 모든 경기)

## 로컬 실행
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

FastAPI:
```bash
uvicorn app.main:app --reload
```

Streamlit:
```bash
streamlit run streamlit_mlb.py --server.port 8501
```

## Streamlit Cloud 배포
1. [share.streamlit.io](https://share.streamlit.io) 로그인
2. `New app`
3. Repository: `volt239/MLB`
4. Branch: `main`
5. Main file path: `streamlit_mlb.py`
6. Deploy

## Render 배포 (FastAPI)
- `render.yaml` 사용해서 Blueprint 배포 가능
