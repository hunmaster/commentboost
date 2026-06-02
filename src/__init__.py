"""CommentBoost 핵심 모듈 패키지."""
import os
import sys

# Windows 한국어 환경 (cp949) 인코딩 문제 해결
# rich 라이브러리의 유니코드 박스 문자(╔, ╗ 등)가 cp949에서 인코딩 실패하는 문제 방지
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for _s in (sys.stdout, sys.stderr):
        if _s and hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
