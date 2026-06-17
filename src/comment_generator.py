"""
AI 댓글/대댓글 생성 엔진 (앱 내장 — n8n '닥터두드리 바이럴 v2' 워크플로우 포팅)

3단계 파이프라인:
  1) 영상 분석 (gpt-4o, JSON)  → brand_fit/소구점/공감장면 등 추출
  2) 변수 셔플 (페르소나/오프닝/길이 등 랜덤)
  3) 댓글 생성 (gpt-4o) → 금칙어/브랜드 정규식 검사
  4) 통과 시 대댓글 생성 (gpt-4o)

프롬프트 원문은 src/comment_prompts.json (n8n에서 추출). 모델/브랜드/키는 env로 제어.
"""
import os
import re
import json
import random

_PROMPTS_PATH = os.path.join(os.path.dirname(__file__), "comment_prompts.json")
with open(_PROMPTS_PATH, encoding="utf-8") as _f:
    _PROMPTS = json.load(_f)

DEFAULT_BRAND = "녹백스프레이"

# ── 페르소나/변수 풀 (n8n Shuffle Variables 노드 포팅) ──
PERSONAS = [
    {"p": "30대 워킹맘 아토피 둘째맘", "g": "여", "age": "30대", "tone": "다정 수다체(~더라구요/~거든요)", "quirk": "아이 얘기 자주, ㅠㅠ 가끔"},
    {"p": "40대 남성 콜린성 두드러기 10년차", "g": "남", "age": "40대", "tone": "건조 단답체(~음/~함)", "quirk": "감정표현 적고 사실·수치 위주, 오타 거의 없음"},
    {"p": "20대 여대생 민감성 피부", "g": "여", "age": "20대", "tone": "발랄 구어체(~어요/~잖아요)", "quirk": "ㅋㅋ/ㅠㅠ 의성어 1개, 자연스런 줄임말"},
    {"p": "50대 주부 햇빛 알레르기", "g": "여", "age": "50대", "tone": "정겨운 구어체(~네요/~더라구요)", "quirk": "띄어쓰기 가끔 뭉갬, 말줄임표(...) 자주"},
    {"p": "30대 직장인 여드름 트러블", "g": "남", "age": "30대", "tone": "무심 구어체(~음/~네요 섞임)", "quirk": "짧게 끊어 씀"},
    {"p": "40대 아토피 아동맘", "g": "여", "age": "40대", "tone": "걱정 수다체(~어요/~던데요)", "quirk": "아이 걱정, ㅠㅠ 가끔"},
    {"p": "20대 취준생 스트레스성 두드러기", "g": "남", "age": "20대", "tone": "자조 구어체(~음ㅋㅋ/~네요)", "quirk": "ㅋㅋ 자조, 줄임말"},
    {"p": "60대 폐경기 건조 가려움", "g": "여", "age": "60대", "tone": "점잖은 존댓말(~습니다/~네요)", "quirk": "띄어쓰기·오타 가끔(키패드), 말줄임표"},
    {"p": "30대 남성 야근러 땀띠", "g": "남", "age": "30대", "tone": "건조 구어체(~음/~함/~네)", "quirk": "피곤함 묻어남, 짧게"},
    {"p": "40대 워킹맘 선크림 알레르기", "g": "여", "age": "40대", "tone": "바쁜 다정체(~어요/~더라구요)", "quirk": "말줄임표, 빠르게 친 느낌"},
    {"p": "20대 헬스 트레이너 콜린성 두드러기", "g": "남", "age": "20대", "tone": "활기 구어체(~어요/~거든요)", "quirk": "운동 얘기, ㅋㅋ 가끔"},
    {"p": "50대 갱년기 피부 발적", "g": "여", "age": "50대", "tone": "하소연 구어체(~네요/~더라구요ㅠ)", "quirk": "띄어쓰기 뭉갬, ... 자주"},
    {"p": "30대 프리랜서 재택러 환절기 가려움", "g": "여", "age": "30대", "tone": "담백 구어체(~어요/~네요)", "quirk": "차분, 이모지 거의 없음"},
    {"p": "40대 직장맘 새집증후군 피부발적", "g": "여", "age": "40대", "tone": "걱정 수다체(~던데요/~거든요)", "quirk": "아이·집 얘기"},
    {"p": "20대 군필 복학생 땀 알레르기", "g": "남", "age": "20대", "tone": "무심 반말섞임체(~음/~네ㅋㅋ)", "quirk": "ㅋㅋ, 줄임말, 짧게"},
    {"p": "50대 자영업자 손등 습진", "g": "남", "age": "50대", "tone": "투박 단답체(~음/~네)", "quirk": "띄어쓰기 가끔 틀림, 무뚝뚝"},
    {"p": "30대 임산부 호르몬 두드러기", "g": "여", "age": "30대", "tone": "조심 다정체(~어요/~더라구요)", "quirk": "임신 얘기, ㅠㅠ 가끔"},
    {"p": "40대 골프광 햇빛 화끈거림", "g": "남", "age": "40대", "tone": "호탕 구어체(~네요/~더라구요)", "quirk": "취미 얘기, 짧고 시원하게"},
]
HOOK_TYPES = ["개인경험", "질문던지기", "공감한줄", "반전고백", "숫자충격", "어그로더보기", "체념탄식", "선배조언"]
OPENING_STYLES = ["질문형", "체념탄식", "타인언급", "상황서술", "수치인용", "반전고백", "개인경험", "어그로"]
CTA_TONES = ["혼잣말", "추천", "후기", "고민상담", "더보기유발", "질문유발"]
LENGTHS = ["짧음(2-3문장)", "중간(4-5문장)", "길게(6-7문장)"]
EMOJI_LEVELS = [0, 0, 1]
BRAND_MENTION_STYLES = ["직접언급", "직접언급", "직접언급", "성분으로 돌려", "제형으로 돌려"]
COMMENT_MODES = ["solo", "solo", "multi"]
REACTION_TYPES = ["agree", "question", "add_info"]

# 금칙어 (n8n Regex Check 포팅)
BANNED_RE = re.compile(
    r"(저분자라 흡수|흡수가 빨라|기력 회복|알아보세요|검색해\s*보세요|찾아보세요|"
    r"추천드려요|강추|진짜 레전드|여러분|안녕하세요|BSASM|30,?000PPM|특허원료|임상테스트)",
    re.IGNORECASE,
)

_EXPR_RE = re.compile(r"\{\{\s*\$json\.([^\s}]+)\s*\}\}")


def _pick(arr):
    return arr[random.randrange(len(arr))]


def _pick_n(arr, n):
    copy = list(arr)
    out = []
    for _ in range(min(n, len(copy))):
        out.append(copy.pop(random.randrange(len(copy))))
    return out


def _as_arr(x):
    if isinstance(x, list):
        return x
    return [str(x)] if x else []


def _join_arr(x, sep=" / "):
    return sep.join(str(i) for i in _as_arr(x))


def _render(template, ctx):
    """n8n 표현식 {{ $json.field }} 를 ctx 값으로 치환. 선행 '=' (n8n 표현식 모드) 제거."""
    if template.startswith("="):
        template = template[1:]
    return _EXPR_RE.sub(lambda m: str(ctx.get(m.group(1), "")), template)


def _brand_regex(brand):
    esc = re.escape(brand).replace(r"\ ", r"\s*")
    return re.compile(esc, re.IGNORECASE)


def _model():
    return os.getenv("OPENAI_COMMENT_MODEL", "gpt-4o")


def _chat(system, user, temperature, max_tokens, top_p=None, api_key=None, model=None):
    """OpenAI Chat Completions 호출. (n8n langchain openAi 노드 = system+user 메시지)"""
    from openai import OpenAI

    api_key = api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY가 설정되지 않았습니다. 설정 탭에서 OpenAI API 키를 입력하세요.")
    client = OpenAI(api_key=api_key)
    kwargs = dict(
        model=model or _model(),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    if top_p is not None:
        kwargs["top_p"] = top_p
    resp = client.chat.completions.create(**kwargs)
    return (resp.choices[0].message.content or "").strip()


def _analysis_user(keyword, title, description, url):
    raw = _PROMPTS["analysis_user_raw"]
    idx = raw.find("위 유튜브 영상의 맥락을")
    static = raw[idx:] if idx >= 0 else raw
    header = (
        f"영상 검색 키워드: {keyword}\n"
        f"영상 제목: {title}\n"
        f"영상 설명(제목+더보기): {description}\n"
        f"영상 링크: {url}\n\n"
    )
    return header + static


def _parse_analysis(text):
    cleaned = re.sub(r"```json\n?|\n?```", "", str(text or "")).strip()
    try:
        return json.loads(cleaned)
    except Exception:
        return {}


def judge(title, a, b, api_key=None, model=None):
    """두 댓글 세트(A=n8n, B=앱)를 공정하게 비교 평가. 점수 dict 반환.

    a/b: {"comment": str, "reply": str}
    반환: {a:{naturalness,empathy,brand_fit,engagement,total}, b:{...}, winner, reason}
          실패 시 {"error": "..."}
    """
    system = ("너는 한국어 유튜브 댓글 마케팅 품질 평가자다. 두 후보(A, B)를 공정하게 비교하고 "
              "오직 JSON만 출력한다. 설명·코드블록 금지.")
    user = (
        f"영상 제목: {title}\n\n"
        f"[A 후보]\n댓글: {a.get('comment','')}\n대댓글: {a.get('reply','')}\n\n"
        f"[B 후보]\n댓글: {b.get('comment','')}\n대댓글: {b.get('reply','')}\n\n"
        "각 후보를 1~10 정수로 평가:\n"
        "- naturalness(사람이 쓴 듯 자연스러움)\n"
        "- empathy(공감/진정성)\n"
        "- brand_fit(브랜드 노출이 거슬리지 않고 자연스러운가)\n"
        "- engagement(참여·호기심 유도)\n"
        'JSON 형식: {"a":{"naturalness":0,"empathy":0,"brand_fit":0,"engagement":0,"total":0},'
        '"b":{"naturalness":0,"empathy":0,"brand_fit":0,"engagement":0,"total":0},'
        '"winner":"A|B|무승부","reason":"2~3문장 한국어 근거"}'
    )
    try:
        raw = _chat(system, user, 0.2, 600, api_key=api_key, model=model)
    except Exception as e:
        return {"error": str(e)}
    parsed = _parse_analysis(raw)
    if not parsed:
        return {"error": "평가 응답을 해석하지 못했습니다.", "raw": raw[:300]}
    return parsed


def generate(keyword, title, description, url, brand=None, api_key=None, model=None, max_comment_attempts=2):
    """영상 1건 → 댓글/대댓글 생성.

    반환 dict:
      status: '생성완료' | '관련없음'(brand_fit=low) | '실패'(정규식/오류)
      comment_text, reply_text, brand_fit, fit_reason, error
    """
    brand = brand or os.getenv("OPENAI_COMMENT_BRAND") or DEFAULT_BRAND
    try:
        # 1) 영상 분석
        analysis_raw = _chat(_PROMPTS["analysis_system"], _analysis_user(keyword, title, description, url), 0.3, 900, api_key=api_key, model=model)
        v = _parse_analysis(analysis_raw)
        if not v:
            # 분석 JSON 파싱 실패 → 빈 컨텍스트로 댓글 생성하면 저품질. 추가 호출 없이 빠른 실패.
            return {"status": "실패", "comment_text": "", "reply_text": "", "brand_fit": "", "error": "영상 분석 결과 파싱 실패"}
        brand_fit = str(v.get("brand_fit", "high")).lower()
        if brand_fit == "low":
            return {
                "status": "관련없음",
                "comment_text": "",
                "reply_text": "",
                "brand_fit": "low",
                "fit_reason": v.get("fit_reason", ""),
            }

        # 2) 변수 셔플
        alt_arr = _as_arr(v.get("existing_alternatives"))
        routine_arr = _as_arr(v.get("care_routine"))
        existing_alternatives = ", ".join(_pick_n(alt_arr, 3) if alt_arr else ["병원 처방약", "시중 보습제", "연고"])
        care_routine = ", ".join(_pick_n(routine_arr, 3) if routine_arr else ["미지근한 물 세안", "보습 자주", "자극 줄이기"])
        persona = _pick(PERSONAS)
        reply_persona = _pick([x for x in PERSONAS if x["p"] != persona["p"]])

        ctx = {
            "영상제목": title,
            "영상설명": description,
            "영상링크": url,
            "summary": v.get("summary", ""),
            "key_points": _join_arr(v.get("key_points")),
            "emotional_hook": v.get("emotional_hook", ""),
            "relatable_moments": _join_arr(v.get("relatable_moments")),
            "specific_details": _join_arr(v.get("specific_details")),
            "viewer_pain_journey": v.get("viewer_pain_journey", ""),
            "target_audience": v.get("target_audience", ""),
            "main_condition": v.get("main_condition") or v.get("소구점", ""),
            "소구점": v.get("소구점", ""),
            "desire_axis": v.get("desire_axis", "disease"),
            "brand_fit": brand_fit,
            "bridge_point": v.get("bridge_point", ""),
            "fit_reason": v.get("fit_reason", ""),
            "persona": persona["p"],
            "persona_gender": persona["g"],
            "persona_age": persona["age"],
            "persona_tone": persona["tone"],
            "persona_quirk": persona["quirk"],
            "hook_type": _pick(HOOK_TYPES),
            "opening_style": _pick(OPENING_STYLES),
            "cta_tone": _pick(CTA_TONES),
            "length": _pick(LENGTHS),
            "emoji_level": _pick(EMOJI_LEVELS),
            "brand_mention_style": _pick(BRAND_MENTION_STYLES),
            "comment_mode": _pick(COMMENT_MODES),
            "existing_alternatives": existing_alternatives,
            "brand_shuffle": care_routine,
            "care_routine": care_routine,
            "reply_persona": reply_persona["p"],
            "reply_tone": reply_persona["tone"],
            "reply_quirk": reply_persona["quirk"],
            "reaction_type": _pick(REACTION_TYPES),
            "reply_brand_mention": random.random() > 0.5,
            "primary_brand": brand,
        }

        # 3) 댓글 생성 + 금칙어/브랜드 정규식 검사 (실패 시 재시도)
        comment_text = ""
        passed = False
        brand_re = _brand_regex(brand)
        for _ in range(max(1, max_comment_attempts)):
            comment_text = _chat(_render(_PROMPTS["comment_system"], ctx), _render(_PROMPTS["comment_user"], ctx), 0.95, 700, top_p=0.95, api_key=api_key, model=model)
            if not BANNED_RE.search(comment_text) and brand_re.search(comment_text):
                passed = True
                break
        if not passed:
            return {
                "status": "실패",
                "comment_text": comment_text,
                "reply_text": "",
                "brand_fit": brand_fit,
                "error": "정규식 검사 실패(금칙어 포함 또는 브랜드 미언급)",
            }

        # 4) 대댓글 생성
        ctx["comment_text"] = comment_text
        reply_text = _chat(_render(_PROMPTS["reply_system"], ctx), _render(_PROMPTS["reply_user"], ctx), 0.85, 300, api_key=api_key, model=model)

        return {
            "status": "생성완료",
            "comment_text": comment_text,
            "reply_text": reply_text,
            "brand_fit": brand_fit,
            "fit_reason": v.get("fit_reason", ""),
        }
    except Exception as e:
        return {"status": "실패", "comment_text": "", "reply_text": "", "brand_fit": "", "error": str(e)}
