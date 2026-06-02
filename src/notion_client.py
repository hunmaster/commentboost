"""
Notion API 연동 모듈
- 데이터베이스에서 댓글 작업 목록 읽기
- 댓글 URL 결과 저장
"""

import os
import sys
from dotenv import load_dotenv
from notion_client import Client
from rich.console import Console

console = Console()


class NotionManager:
    def __init__(self, token=None, database_id=None):
        # .env는 os.environ에 없는 키만 보충 (override=False)
        # → _ensure_notion_env()가 이미 설정한 값을 덮어쓰지 않음
        load_dotenv(override=False)

        self.token = token or os.getenv("NOTION_API_TOKEN")
        self.database_id = database_id or os.getenv("NOTION_DATABASE_ID")

        if not self.token or not self.database_id:
            raise ValueError("NOTION_API_TOKEN과 NOTION_DATABASE_ID를 .env에 설정해주세요.")

        self.client = Client(auth=self.token)

        # 컬럼명 설정 (노션 DB 실제 컬럼명 기준)
        self.col_youtube_url = os.getenv("NOTION_COLUMN_YOUTUBE_URL", "영상 링크")
        self.col_comment_text = os.getenv("NOTION_COLUMN_COMMENT_TEXT", "댓글 원고")
        self.col_result_url = os.getenv("NOTION_COLUMN_COMMENT_RESULT_URL", "댓글 url")
        self.col_status = os.getenv("NOTION_COLUMN_STATUS", "상태")
        self.col_account = os.getenv("NOTION_COLUMN_ACCOUNT", "댓글 계정")
        self.col_reply_text = os.getenv("NOTION_COLUMN_REPLY_TEXT", "대댓글 원고")

        # DB 스키마에서 실제 속성명 확인 및 자동 매칭
        self._db_properties = {}
        self._resolve_column_names()

        console.print(f"[dim]컬럼 설정: url='{self.col_youtube_url}', comment='{self.col_comment_text}', "
                      f"result='{self.col_result_url}', status='{self.col_status}', account='{self.col_account}'[/dim]")

    def _resolve_column_names(self):
        """DB 스키마를 조회하여 실제 속성명과 매칭합니다 (대소문자 무시)."""
        try:
            db_info = self.client.databases.retrieve(database_id=self.database_id)
            self._db_properties = db_info.get("properties", {})
            prop_names = list(self._db_properties.keys())

            # 대소문자 무시 비교 헬퍼
            def find_exact_or_icase(configured_name, prop_names):
                """설정된 이름이 DB에 정확히 있으면 그대로, 없으면 대소문자 무시로 찾기"""
                if configured_name in prop_names:
                    return configured_name
                configured_lower = configured_name.lower()
                for name in prop_names:
                    if name.lower() == configured_lower:
                        return name
                return None

            # 댓글 URL 컬럼 매칭
            matched = find_exact_or_icase(self.col_result_url, prop_names)
            if matched:
                if matched != self.col_result_url:
                    console.print(f"[yellow]댓글 URL 컬럼 매칭: '{self.col_result_url}' → '{matched}'[/yellow]")
                self.col_result_url = matched
            else:
                # 키워드 기반 자동 탐색 (댓글 + url)
                found = None
                for name in prop_names:
                    name_lower = name.lower()
                    if "댓글" in name and "url" in name_lower:
                        found = name
                        break
                if found:
                    console.print(f"[yellow]댓글 URL 컬럼 자동 탐색: '{self.col_result_url}' → '{found}'[/yellow]")
                    self.col_result_url = found
                else:
                    console.print(f"[yellow]⚠ 댓글 URL 컬럼 '{self.col_result_url}'이 DB에 없습니다. URL 저장이 생략됩니다.[/yellow]")
                    self.col_result_url = None

            # 상태 컬럼 매칭
            matched = find_exact_or_icase(self.col_status, prop_names)
            if matched:
                self.col_status = matched
            else:
                for name in prop_names:
                    if "상태" in name or "status" in name.lower():
                        console.print(f"[yellow]상태 컬럼 자동 매칭: '{self.col_status}' → '{name}'[/yellow]")
                        self.col_status = name
                        break

            # 체크박스 컬럼 자동 탐색
            self.col_checkbox = None        # 댓글 완료
            self.col_reply_checkbox = None   # 대댓글 완료
            self.col_like_checkbox = None    # 좋아요 완료
            checkbox_candidates = []
            for name, prop_info in self._db_properties.items():
                if prop_info.get("type") == "checkbox":
                    # 댓글 관련 체크박스
                    if "댓글" in name and "완료" in name:
                        checkbox_candidates.append(name)
                    # 좋아요 관련 체크박스
                    if "좋아요" in name and ("완료" in name or "작업" in name):
                        self.col_like_checkbox = name
            if checkbox_candidates:
                # "대댓글"이 아닌 것을 댓글 완료 체크박스로
                for name in checkbox_candidates:
                    if "대댓글" not in name:
                        self.col_checkbox = name
                        break
                if not self.col_checkbox:
                    self.col_checkbox = checkbox_candidates[0]
                # "대댓글"이 포함된 것을 대댓글 완료 체크박스로
                for name in checkbox_candidates:
                    if "대댓글" in name:
                        self.col_reply_checkbox = name
                        break
            console.print(f"[dim]체크박스 컬럼: 댓글='{self.col_checkbox}', 대댓글='{self.col_reply_checkbox}', 좋아요='{self.col_like_checkbox}'[/dim]")

            # 대댓글 원고 컬럼 자동 탐색
            reply_col = find_exact_or_icase(self.col_reply_text, prop_names)
            if reply_col:
                self.col_reply_text = reply_col
            else:
                # 키워드 기반 자동 탐색 (대댓글 + 원고/텍스트)
                for name in prop_names:
                    if "대댓글" in name and ("원고" in name or "텍스트" in name or "내용" in name):
                        self.col_reply_text = name
                        break
            console.print(f"[dim]대댓글 원고 컬럼: '{self.col_reply_text}'[/dim]")

            console.print(f"[dim]DB 속성 목록: {', '.join(prop_names)}[/dim]")

            # 상태 컬럼의 실제 옵션값 저장 (상태값 자동 매핑용)
            self._status_options = []
            status_prop = self._db_properties.get(self.col_status, {})
            status_type = status_prop.get("type", "select")
            if status_type in ("select", "status"):
                opts = status_prop.get(status_type, {}).get("options", [])
                self._status_options = [o["name"] for o in opts]
                console.print(f"[dim]상태 옵션값: {self._status_options}[/dim]")
                self._auto_map_statuses()

        except Exception as e:
            error_msg = str(e)
            if "403" in error_msg or "Forbidden" in error_msg:
                console.print(f"[red]노션 DB 접근 권한 없음 (403): Integration이 해당 DB에 연결되어 있는지 확인하세요.[/red]")
                console.print(f"[yellow]해결 방법: 노션 DB 페이지 → 우측 상단 '...' → '연결 추가' → Integration 선택[/yellow]")
            else:
                console.print(f"[yellow]DB 스키마 조회 실패 (기본 컬럼명 사용): {e}[/yellow]")
            self.col_checkbox = "댓글 완료"  # 기본값
            self.col_reply_checkbox = "대댓글 완료"  # 기본값
            self.col_like_checkbox = "좋아요 완료"  # 기본값

    def _auto_map_statuses(self):
        """DB의 실제 상태 옵션값과 코드에서 사용하는 상태명을 자동 매핑합니다.
        예: DB에 '댓글 작업전'이 있으면 코드의 '댓글작업전'과 매핑."""
        if not hasattr(self, '_status_options') or not self._status_options:
            return

        # 코드에서 사용하는 상태명 → DB 실제 옵션명 매핑
        self._status_map = {}
        code_statuses = ["댓글작업전", "댓글완료", "대댓글완료", "좋아요작업완료", "에러", "중복"]

        for code_status in code_statuses:
            # 정확한 매칭 먼저
            if code_status in self._status_options:
                self._status_map[code_status] = code_status
                continue
            # 공백 제거 후 비교
            code_no_space = code_status.replace(" ", "")
            for opt in self._status_options:
                if opt.replace(" ", "") == code_no_space:
                    self._status_map[code_status] = opt
                    console.print(f"[dim]상태 매핑: '{code_status}' → '{opt}'[/dim]")
                    break

    def get_pending_tasks(self):
        """상태가 '댓글작업전' 또는 비어있는 작업을 전부 가져옵니다 (페이지네이션).
        상태가 비어있고 원고가 있으면 → '댓글작업전'으로 자동 설정.
        댓글 원고가 비어있으면 스킵합니다.
        """
        all_tasks = self._get_all_tasks_by_status("댓글작업전")

        valid_tasks = []
        no_script_count = 0
        auto_set_count = 0
        for task in all_tasks:
            comment_text = (task.get("comment_text") or "").strip()
            status = (task.get("status") or "").strip()

            if not comment_text:
                # 원고 없음 → 스킵
                no_script_count += 1
            else:
                # 원고 있음
                if not status:
                    # 상태가 비어있으면 → '댓글작업전'으로 자동 설정
                    try:
                        self.update_task_status(task["page_id"], "댓글작업전")
                        auto_set_count += 1
                    except Exception:
                        pass
                valid_tasks.append(task)

        if auto_set_count > 0:
            console.print(f"[green]상태 빈 칸 + 원고 있음 → '댓글작업전' 자동 설정: {auto_set_count}건[/green]")
        if no_script_count > 0:
            console.print(f"[yellow]원고 없는 작업 {no_script_count}건 스킵 (원고 입력 후 자동 포함)[/yellow]")

        return valid_tasks

    def check_and_restore_script_tasks(self):
        """'원고 X' 상태의 작업 중 원고가 채워진 것을 '댓글작업전'으로 복원합니다."""
        try:
            tasks = self._get_all_tasks_by_status("원고 X", include_empty=False)
            restored = 0
            for task in tasks:
                comment_text = (task.get("comment_text") or "").strip()
                if comment_text:
                    try:
                        self.update_task_status(task["page_id"], "댓글작업전")
                        restored += 1
                    except Exception:
                        pass
            if restored > 0:
                console.print(f"[green]원고 채워진 작업 {restored}건 → '댓글작업전'으로 복원[/green]")
            return restored
        except Exception:
            return 0

    def get_reply_pending_tasks(self):
        """상태가 '댓글완료'이고 대댓글 원고가 있는 작업을 가져옵니다."""
        tasks = self._get_all_tasks_by_status("댓글완료")
        reply_tasks = [t for t in tasks if t.get("reply_text") and t.get("result_url")]
        console.print(f"[green]대댓글 대기 작업: {len(reply_tasks)}개 (대댓글 원고+댓글URL 있음)[/green]")
        return reply_tasks

    def _get_all_tasks_by_status(self, status_value, include_empty=None):
        """페이지네이션으로 해당 상태의 작업을 전부 가져옵니다.
        include_empty: True이면 상태가 비어있는 행도 포함 (댓글작업전 조회 시).
        None이면 status_value가 '댓글작업전'일 때 자동으로 True.
        """
        if include_empty is None:
            include_empty = (status_value == "댓글작업전")

        console.print(f"[blue]노션 DB 전체 조회 (상태: '{status_value}'{', +빈칸 포함' if include_empty else ''})[/blue]")

        all_results = []
        has_more = True
        start_cursor = None

        # select 타입 먼저 시도 — 빈 칸도 포함 시 OR 필터 사용
        if include_empty:
            query_filter = {
                "or": [
                    {"property": self.col_status, "select": {"equals": status_value}},
                    {"property": self.col_status, "select": {"is_empty": True}},
                ]
            }
        else:
            query_filter = {"property": self.col_status, "select": {"equals": status_value}}
        use_status_type = False

        while has_more:
            try:
                kwargs = {
                    "database_id": self.database_id,
                    "page_size": 100,
                    "filter": query_filter,
                }
                if start_cursor:
                    kwargs["start_cursor"] = start_cursor
                response = self.client.databases.query(**kwargs)
            except Exception:
                if not use_status_type:
                    # status 타입으로 재시도
                    use_status_type = True
                    if include_empty:
                        query_filter = {
                            "or": [
                                {"property": self.col_status, "status": {"equals": status_value}},
                                {"property": self.col_status, "status": {"is_empty": True}},
                            ]
                        }
                    else:
                        query_filter = {"property": self.col_status, "status": {"equals": status_value}}
                    try:
                        kwargs["filter"] = query_filter
                        response = self.client.databases.query(**kwargs)
                    except Exception as e2:
                        console.print(f"[red]노션 조회 실패: {e2}[/red]")
                        break
                else:
                    break

            all_results.extend(response.get("results", []))
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

            if has_more:
                console.print(f"[dim]  {len(all_results)}건 로드, 추가 데이터 있음...[/dim]")

        console.print(f"[green]전체 조회 완료: {len(all_results)}건[/green]")

        tasks = []
        for page in all_results:
            task = self._parse_page(page)
            if task:
                tasks.append(task)

        console.print(f"[green]'{status_value}' 작업: {len(tasks)}개{' (빈칸 포함)' if include_empty else ''}[/green]")
        return tasks

    def count_pending_tasks(self):
        """댓글작업전 상태의 전체 개수만 빠르게 세서 반환합니다."""
        return self._count_by_status("댓글작업전")

    def count_all_statuses(self):
        """대시보드용: 주요 상태별 개수를 한 번에 반환합니다.
        전체 DB를 1회만 조회하여 상태별로 분류합니다."""
        console.print("[blue]대시보드 카운트 조회 (전체 1회 조회)[/blue]")
        counts = {}
        has_more = True
        start_cursor = None

        while has_more:
            try:
                kwargs = {"database_id": self.database_id, "page_size": 100}
                if start_cursor:
                    kwargs["start_cursor"] = start_cursor
                response = self.client.databases.query(**kwargs)
                for page in response.get("results", []):
                    props = page.get("properties", {})
                    status_prop = props.get(self.col_status, {})
                    status_type = status_prop.get("type", "select")
                    if status_type == "select":
                        sv = (status_prop.get("select") or {}).get("name", "")
                    elif status_type == "status":
                        sv = (status_prop.get("status") or {}).get("name", "")
                    else:
                        sv = ""
                    # 빈 칸은 "댓글작업전"으로 간주
                    if not sv:
                        sv = "댓글작업전"
                    counts[sv] = counts.get(sv, 0) + 1
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")
            except Exception as e:
                console.print(f"[red]카운트 조회 실패: {e}[/red]")
                break

        console.print(f"[green]상태별 카운트: {counts}[/green]")
        return counts

    def _count_by_status(self, status_value):
        """특정 상태의 개수를 빠르게 셉니다. 댓글작업전은 빈 칸도 포함."""
        count = 0
        has_more = True
        start_cursor = None
        include_empty = (status_value == "댓글작업전")
        if include_empty:
            query_filter = {
                "or": [
                    {"property": self.col_status, "select": {"equals": status_value}},
                    {"property": self.col_status, "select": {"is_empty": True}},
                ]
            }
        else:
            query_filter = {"property": self.col_status, "select": {"equals": status_value}}

        while has_more:
            try:
                kwargs = {"database_id": self.database_id, "page_size": 100, "filter": query_filter}
                if start_cursor:
                    kwargs["start_cursor"] = start_cursor
                response = self.client.databases.query(**kwargs)
                count += len(response.get("results", []))
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")
            except Exception:
                try:
                    if include_empty:
                        kwargs["filter"] = {
                            "or": [
                                {"property": self.col_status, "status": {"equals": status_value}},
                                {"property": self.col_status, "status": {"is_empty": True}},
                            ]
                        }
                    else:
                        kwargs["filter"] = {"property": self.col_status, "status": {"equals": status_value}}
                    response = self.client.databases.query(**kwargs)
                    count += len(response.get("results", []))
                    has_more = response.get("has_more", False)
                    start_cursor = response.get("next_cursor")
                except Exception:
                    break
        return count

    # 체크박스 기반 AND 필터링 매핑
    # 실제 작업 흐름: 댓글 → 좋아요(즉시) → (나중에) 대댓글
    # 탭 이름 → (체크=True 컬럼 attr, 체크=False 컬럼 attr)
    CHECKBOX_FILTER_MAP = {
        "댓글완료":      ("col_checkbox",       "col_reply_checkbox"),    # 댓글✓ AND 대댓글✗ (대댓글 대기)
        "대댓글완료":    ("col_reply_checkbox",  None),                   # 대댓글✓ (모든 작업 완료)
        "좋아요작업완료": ("col_like_checkbox",   None),                   # 좋아요✓ (좋아요 주문 확인)
    }

    def _is_checkbox_filterable(self, status_value):
        """해당 상태값이 체크박스 기반 필터링이 가능한지 확인합니다."""
        if status_value not in self.CHECKBOX_FILTER_MAP:
            return False
        true_attr, _ = self.CHECKBOX_FILTER_MAP[status_value]
        return getattr(self, true_attr, None) is not None

    def get_tasks_by_checkbox(self, status_value, date_filter=None, progress_callback=None, max_results=0):
        """체크박스 AND 로직으로 작업 목록을 필터링합니다.
        - 댓글완료: 댓글완료✓ AND 대댓글완료✗
        - 대댓글완료: 대댓글완료✓ AND 좋아요완료✗
        - 좋아요작업완료: 좋아요완료✓
        max_results: 0이면 전체 로드, 양수면 해당 건수까지만 로드
        """
        true_attr, false_attr = self.CHECKBOX_FILTER_MAP[status_value]
        true_col = getattr(self, true_attr)
        false_col = getattr(self, false_attr, None) if false_attr else None

        console.print(f"[blue]노션 DB 체크박스 조회 ('{status_value}': {true_col}=✓"
                      f"{f', {false_col}=✗' if false_col else ''}"
                      f", 날짜: {date_filter or '전체'})[/blue]")

        def build_filter():
            conditions = [{"property": true_col, "checkbox": {"equals": True}}]
            if false_col:
                conditions.append({"property": false_col, "checkbox": {"equals": False}})
            if date_filter:
                if date_filter.startswith("since:"):
                    since_date = date_filter.split(":", 1)[1]
                    conditions.append({"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": f"{since_date}T00:00:00+09:00"}})
                else:
                    conditions.append({"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": f"{date_filter}T00:00:00+09:00"}})
                    conditions.append({"timestamp": "last_edited_time", "last_edited_time": {"before": f"{date_filter}T23:59:59+09:00"}})
            return {"and": conditions} if len(conditions) > 1 else conditions[0]

        # 페이지네이션으로 전체 조회
        all_results = []
        has_more = True
        start_cursor = None

        while has_more:
            try:
                kwargs = {"database_id": self.database_id, "page_size": 100, "filter": build_filter()}
                if start_cursor:
                    kwargs["start_cursor"] = start_cursor
                response = self.client.databases.query(**kwargs)
            except Exception as e:
                console.print(f"[red]체크박스 조회 실패: {e}[/red]")
                console.print(f"[yellow]상태 컬럼 기반으로 폴백합니다.[/yellow]")
                return self.get_tasks_by_status(status_value, date_filter, progress_callback, max_results=max_results)

            all_results.extend(response.get("results", []))
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

            # max_results 제한 확인
            if max_results > 0 and len(all_results) >= max_results:
                all_results = all_results[:max_results]
                has_more = False

            if has_more:
                console.print(f"[dim]  {len(all_results)}건 로드, 추가 데이터 있음...[/dim]")
            if progress_callback:
                progress_callback(len(all_results), f"{len(all_results)}건 로드 중...")

        console.print(f"[blue]체크박스 조회 완료: {len(all_results)}건[/blue]")

        tasks = []
        for idx, page in enumerate(all_results):
            task = self._parse_page(page, debug=(idx == 0))
            if task:
                tasks.append(task)

        if progress_callback:
            progress_callback(len(tasks), f"완료: {len(tasks)}건")
        console.print(f"[green]'{status_value}' 체크박스 필터 작업: {len(tasks)}개[/green]")
        return tasks

    def get_tasks_by_status(self, status_value, date_filter=None, progress_callback=None, max_results=0):
        """지정된 상태의 작업 목록을 페이지네이션으로 가져옵니다.
        체크박스 컬럼이 있는 경우 체크박스 AND 로직으로 자동 전환됩니다.
        date_filter: 'YYYY-MM-DD' (특정 일자) 또는 'since:YYYY-MM-DD' (이후 전체)
        max_results: 0이면 전체 로드, 양수면 해당 건수까지만 로드
        """
        # 체크박스 기반 필터링이 가능한 상태면 자동 전환
        if self._is_checkbox_filterable(status_value):
            return self.get_tasks_by_checkbox(status_value, date_filter, progress_callback, max_results=max_results)

        console.print(f"[blue]노션 DB 조회 (상태: '{status_value}', 날짜: {date_filter or '전체'})[/blue]")

        # 상태 필터 구성
        def build_filter(use_status_type=False):
            st = "status" if use_status_type else "select"
            sf = {"property": self.col_status, st: {"equals": status_value}}
            if date_filter:
                if date_filter.startswith("since:"):
                    # 범위 필터: since:YYYY-MM-DD → 해당 날짜 이후 전체
                    since_date = date_filter.split(":", 1)[1]
                    return {
                        "and": [
                            sf,
                            {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": f"{since_date}T00:00:00+09:00"}},
                        ]
                    }
                else:
                    # 특정 일자 필터
                    return {
                        "and": [
                            sf,
                            {"timestamp": "last_edited_time", "last_edited_time": {"on_or_after": f"{date_filter}T00:00:00+09:00"}},
                            {"timestamp": "last_edited_time", "last_edited_time": {"before": f"{date_filter}T23:59:59+09:00"}},
                        ]
                    }
            return sf

        # 페이지네이션으로 전체 조회
        all_results = []
        has_more = True
        start_cursor = None
        use_status_type = False

        while has_more:
            try:
                kwargs = {"database_id": self.database_id, "page_size": 100, "filter": build_filter(use_status_type)}
                if start_cursor:
                    kwargs["start_cursor"] = start_cursor
                response = self.client.databases.query(**kwargs)
            except Exception:
                if not use_status_type:
                    use_status_type = True
                    try:
                        kwargs["filter"] = build_filter(use_status_type)
                        if start_cursor:
                            kwargs["start_cursor"] = start_cursor
                        response = self.client.databases.query(**kwargs)
                    except Exception as e2:
                        console.print(f"[red]노션 조회 실패: {e2}[/red]")
                        break
                else:
                    break

            all_results.extend(response.get("results", []))
            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

            # max_results 제한 확인
            if max_results > 0 and len(all_results) >= max_results:
                all_results = all_results[:max_results]
                has_more = False

            if has_more:
                console.print(f"[dim]  {len(all_results)}건 로드, 추가 데이터 있음...[/dim]")
            if progress_callback:
                progress_callback(len(all_results), f"{len(all_results)}건 로드 중...")

        truncated = max_results > 0 and len(all_results) >= max_results
        console.print(f"[blue]전체 조회 완료: {len(all_results)}건{' (제한됨)' if truncated else ''}[/blue]")

        tasks = []
        for idx, page in enumerate(all_results):
            task = self._parse_page(page, debug=(idx == 0))
            if task:
                tasks.append(task)

        if progress_callback:
            progress_callback(len(tasks), f"완료: {len(tasks)}건")
        console.print(f"[green]'{status_value}' 작업: {len(tasks)}개[/green]")
        return tasks

    def get_all_tasks(self, progress_callback=None):
        """전체 리스트: 상태 무관하게 DB의 모든 작업을 페이지네이션으로 가져옵니다."""
        console.print("[blue]노션 DB 전체 리스트 조회[/blue]")

        all_results = []
        has_more = True
        start_cursor = None

        while has_more:
            try:
                kwargs = {"database_id": self.database_id, "page_size": 100}
                if start_cursor:
                    kwargs["start_cursor"] = start_cursor
                response = self.client.databases.query(**kwargs)
                all_results.extend(response.get("results", []))
                has_more = response.get("has_more", False)
                start_cursor = response.get("next_cursor")
                if has_more:
                    console.print(f"[dim]  {len(all_results)}건 로드, 추가 데이터 있음...[/dim]")
                if progress_callback:
                    progress_callback(len(all_results), f"{len(all_results)}건 로드 중...")
            except Exception as e:
                console.print(f"[red]전체 리스트 조회 실패: {e}[/red]")
                break

        console.print(f"[blue]전체 리스트: {len(all_results)}건[/blue]")

        tasks = []
        for page in all_results:
            task = self._parse_page(page)
            if task:
                tasks.append(task)

        if progress_callback:
            progress_callback(len(tasks), f"완료: {len(tasks)}건")
        return tasks

    def _parse_page(self, page, debug=False):
        """Notion 페이지에서 필요한 데이터를 추출합니다."""
        props = page.get("properties", {})
        task = {"page_id": page["id"]}

        if debug:
            prop_names = {name: prop.get("type", "?") for name, prop in props.items()}
            console.print(f"[dim]  실제 property 이름: {prop_names}[/dim]")

        # 유튜브 링크 추출 - 지정된 컬럼명으로 먼저 시도, 없으면 url 타입 자동 탐색
        youtube_prop = props.get(self.col_youtube_url, {})
        task["youtube_url"] = self._extract_url(youtube_prop)
        if not task["youtube_url"]:
            # url 타입 컬럼을 자동으로 찾기
            for name, prop in props.items():
                if prop.get("type") == "url" and prop.get("url"):
                    task["youtube_url"] = prop.get("url", "")
                    if debug:
                        console.print(f"[yellow]  URL 자동 탐색: '{name}' → {task['youtube_url'][:50]}[/yellow]")
                    break

        # 댓글 원고 추출
        comment_prop = props.get(self.col_comment_text, {})
        task["comment_text"] = self._extract_text(comment_prop)

        # 대댓글 원고 추출
        reply_prop = props.get(self.col_reply_text, {})
        task["reply_text"] = self._extract_text(reply_prop)

        # 상태 추출
        status_prop = props.get(self.col_status, {})
        task["status"] = self._extract_status(status_prop)

        # 계정 추출 - 지정된 컬럼명으로 먼저, 없으면 '계정' 포함 컬럼 자동 탐색
        account_prop = props.get(self.col_account, {})
        task["account"] = self._extract_text(account_prop)
        if not task["account"]:
            for name, prop in props.items():
                if "계정" in name and prop.get("type") == "select":
                    task["account"] = self._extract_text(prop)
                    if debug and task["account"]:
                        console.print(f"[yellow]  계정 자동 탐색: '{name}' → {task['account']}[/yellow]")
                    break

        # 기존 댓글 URL 확인 - 지정된 컬럼명으로 먼저, 없으면 '댓글' + 'url' 포함 컬럼 탐색
        result_prop = props.get(self.col_result_url, {})
        task["result_url"] = self._extract_url(result_prop)
        if not task["result_url"]:
            for name, prop in props.items():
                if "댓글" in name and "url" in name.lower():
                    task["result_url"] = self._extract_url(prop) or self._extract_text(prop)
                    if debug and task["result_url"]:
                        console.print(f"[yellow]  댓글URL 자동 탐색: '{name}' → {task['result_url'][:50]}[/yellow]")
                    break

        # 영상 제목 추출 (title 타입 컬럼)
        for name, prop in props.items():
            if prop.get("type") == "title":
                task["video_title"] = self._extract_text(prop)
                break
        if "video_title" not in task:
            task["video_title"] = ""

        # 브랜드 추출
        brand_prop = props.get("브랜드", {})
        task["brand"] = self._extract_text(brand_prop)

        # 체크박스 상태 추출
        if self.col_checkbox:
            cb_prop = props.get(self.col_checkbox, {})
            task["comment_done"] = cb_prop.get("checkbox", False) if cb_prop.get("type") == "checkbox" else False
        if self.col_reply_checkbox:
            cb_prop = props.get(self.col_reply_checkbox, {})
            task["reply_done"] = cb_prop.get("checkbox", False) if cb_prop.get("type") == "checkbox" else False
        if self.col_like_checkbox:
            cb_prop = props.get(self.col_like_checkbox, {})
            task["like_done"] = cb_prop.get("checkbox", False) if cb_prop.get("type") == "checkbox" else False

        # 최종 편집 일시 추출
        task["last_edited"] = page.get("last_edited_time", "")

        return task

    def _extract_url(self, prop):
        """속성에서 URL을 추출합니다."""
        prop_type = prop.get("type", "")

        if prop_type == "url":
            return prop.get("url", "")
        elif prop_type == "rich_text":
            texts = prop.get("rich_text", [])
            if texts:
                text = texts[0].get("plain_text", "")
                if text.startswith("http"):
                    return text
                # 링크가 href에 있을 수도 있음
                href = texts[0].get("href", "")
                if href:
                    return href
            return ""
        elif prop_type == "title":
            titles = prop.get("title", [])
            if titles:
                text = titles[0].get("plain_text", "")
                if text.startswith("http"):
                    return text
            return ""
        return ""

    def _extract_text(self, prop):
        """속성에서 텍스트를 추출합니다."""
        prop_type = prop.get("type", "")

        if prop_type == "rich_text":
            texts = prop.get("rich_text", [])
            return "".join(t.get("plain_text", "") for t in texts)
        elif prop_type == "title":
            titles = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in titles)
        elif prop_type == "select":
            select = prop.get("select")
            return select.get("name", "") if select else ""
        return ""

    def _extract_status(self, prop):
        """속성에서 상태를 추출합니다."""
        prop_type = prop.get("type", "")

        if prop_type == "status":
            status = prop.get("status")
            return status.get("name", "") if status else ""
        elif prop_type == "select":
            select = prop.get("select")
            return select.get("name", "") if select else ""
        elif prop_type == "rich_text":
            return self._extract_text(prop)
        return ""

    def _build_result_url_property(self, comment_url):
        """댓글 URL 속성을 DB 컬럼 타입에 맞게 생성합니다 (rich_text 타입)."""
        return {
            "rich_text": [
                {"type": "text", "text": {"content": comment_url, "link": {"url": comment_url}}}
            ]
        }

    def _build_result_url_rich_text(self, comment_url):
        """댓글 URL을 rich_text 형식으로 생성합니다."""
        return {
            "rich_text": [
                {"type": "text", "text": {"content": comment_url, "link": {"url": comment_url}}}
            ]
        }

    def update_task_result(self, page_id, comment_url, status="댓글완료"):
        """댓글 작성 결과를 Notion에 저장합니다.
        상태 + 댓글 URL + 체크박스를 한번에 업데이트합니다.
        Returns:
            (True, "") on success
            (False, "에러 메시지") on failure
        """
        self.last_error = ""

        # 공통 속성 구성
        save_url = bool(comment_url and self.col_result_url)
        checkbox_name = getattr(self, "col_checkbox", None)
        set_checkbox = (status == "댓글완료" and checkbox_name)

        def build_props(status_type, include_url=True, include_checkbox=True):
            """status_type: 'select' 또는 'status'"""
            props = {}
            if status_type == "select":
                props[self.col_status] = {"select": {"name": status}}
            else:
                props[self.col_status] = {"status": {"name": status}}
            if include_url and save_url:
                props[self.col_result_url] = self._build_result_url_property(comment_url)
            if include_checkbox and set_checkbox:
                props[checkbox_name] = {"checkbox": True}
            return props

        # 시도 순서: (status_type, include_url, include_checkbox, 설명)
        attempts = [
            ("select", True, True, "select+url+checkbox"),
            ("select", True, False, "select+url"),
            ("select", False, True, "select+checkbox"),
            ("status", True, True, "status+url+checkbox"),
            ("status", False, True, "status+checkbox"),
            ("select", False, False, "select만"),
            ("status", False, False, "status만"),
        ]

        last_err = ""
        for status_type, inc_url, inc_cb, desc in attempts:
            props = build_props(status_type, inc_url, inc_cb)
            try:
                self.client.pages.update(page_id=page_id, properties=props)
                console.print(f"[green]Notion 업데이트 완료 ({desc}): {status}[/green]")
                # 성공했지만 일부 포함 못 한 항목이 있으면 별도 시도
                if save_url and not inc_url:
                    self._try_update_url_only(page_id, comment_url)
                if set_checkbox and not inc_cb:
                    self._try_update_checkbox_only(page_id)
                return (True, "")
            except Exception as e:
                err = str(e)[:150]
                last_err = err
                console.print(f"[yellow]시도({desc}) 실패: {err}[/yellow]")
                # URL 속성이 DB에 없는 경우 → URL 포함 시도 모두 건너뜀
                if "is not a property" in str(e) and inc_url and save_url:
                    console.print(f"[yellow]'{self.col_result_url}' 속성이 DB에 없음 → URL 저장 비활성화[/yellow]")
                    self.col_result_url = None
                    save_url = False
                    continue

        self.last_error = f"노션 업데이트 완전 실패: {last_err}"
        console.print(f"[red]{self.last_error}[/red]")
        return (False, self.last_error)

    def _try_update_url_only(self, page_id, comment_url):
        """댓글 URL만 별도 업데이트 시도 (실패해도 무시)."""
        if not self.col_result_url or not comment_url:
            return
        try:
            self.client.pages.update(
                page_id=page_id,
                properties={self.col_result_url: self._build_result_url_property(comment_url)},
            )
            console.print(f"[dim]댓글 URL 별도 저장 완료[/dim]")
        except Exception:
            console.print(f"[yellow]댓글 URL 별도 저장 실패 (무시)[/yellow]")

    def _try_update_checkbox_only(self, page_id):
        """체크박스만 별도 업데이트 시도 (실패해도 무시)."""
        checkbox_name = getattr(self, "col_checkbox", None)
        if not checkbox_name:
            return
        try:
            self.client.pages.update(
                page_id=page_id,
                properties={checkbox_name: {"checkbox": True}},
            )
            console.print(f"[dim]체크박스 '{checkbox_name}' 업데이트됨[/dim]")
        except Exception:
            console.print(f"[dim]체크박스 '{checkbox_name}' 업데이트 실패 (무시)[/dim]")

    def update_like_checkbox(self, page_id):
        """좋아요 완료 체크박스를 체크합니다. 실패해도 무시."""
        like_cb = getattr(self, "col_like_checkbox", None)
        if not like_cb:
            return
        try:
            self.client.pages.update(
                page_id=page_id,
                properties={like_cb: {"checkbox": True}},
            )
            console.print(f"[dim]좋아요 체크박스 '{like_cb}' 업데이트됨[/dim]")
        except Exception:
            console.print(f"[dim]좋아요 체크박스 '{like_cb}' 업데이트 실패 (무시)[/dim]")

    def update_reply_result(self, page_id):
        """대댓글 작성 완료 후 노션 업데이트: 상태→대댓글완료 + 대댓글 완료 체크박스 체크.
        Returns:
            (True, "") on success
            (False, "에러 메시지") on failure
        """
        reply_cb = getattr(self, "col_reply_checkbox", None)

        # 시도 순서: select+checkbox, select만, status+checkbox, status만
        attempts = []
        for st in ["select", "status"]:
            if reply_cb:
                attempts.append((st, True))
            attempts.append((st, False))

        last_err = ""
        for status_type, inc_cb in attempts:
            props = {}
            if status_type == "select":
                props[self.col_status] = {"select": {"name": "대댓글완료"}}
            else:
                props[self.col_status] = {"status": {"name": "대댓글완료"}}
            if inc_cb and reply_cb:
                props[reply_cb] = {"checkbox": True}

            try:
                self.client.pages.update(page_id=page_id, properties=props)
                console.print(f"[dim]대댓글 완료 업데이트 성공 ({status_type}{'+cb' if inc_cb else ''})[/dim]")
                return (True, "")
            except Exception as e:
                last_err = str(e)

        # 체크박스만이라도 시도
        if reply_cb:
            try:
                self.client.pages.update(page_id=page_id, properties={reply_cb: {"checkbox": True}})
                console.print(f"[dim]대댓글 체크박스만 업데이트됨[/dim]")
            except Exception:
                pass

        console.print(f"[red]대댓글 완료 업데이트 실패: {last_err}[/red]")
        return (False, last_err)

    def get_completed_video_urls(self):
        """이미 댓글 작업이 완료된 영상들의 video ID 세트를 반환합니다.
        두 가지 방법으로 조회:
        1) '댓글 완료' 체크박스가 True인 항목
        2) 상태가 '댓글완료', '대댓글완료', '좋아요작업완료' 중 하나인 항목
        """
        completed_urls = set()

        # 방법 1: 체크박스 기반
        checkbox_name = getattr(self, "col_checkbox", None)
        if checkbox_name:
            self._collect_video_ids_by_filter(
                {"property": checkbox_name, "checkbox": {"equals": True}},
                completed_urls,
                f"체크박스({checkbox_name})",
            )

        # 방법 2: 상태 기반 (완료 상태들)
        completed_statuses = ["댓글완료", "대댓글완료", "좋아요작업완료"]
        for status in completed_statuses:
            # select 타입 시도
            try:
                self._collect_video_ids_by_filter(
                    {"property": self.col_status, "select": {"equals": status}},
                    completed_urls,
                    f"상태({status})",
                )
            except Exception:
                # status 타입으로 재시도
                try:
                    self._collect_video_ids_by_filter(
                        {"property": self.col_status, "status": {"equals": status}},
                        completed_urls,
                        f"상태({status})",
                    )
                except Exception:
                    pass

        console.print(f"[dim]댓글 완료된 영상: {len(completed_urls)}개[/dim]")
        return completed_urls

    def _collect_video_ids_by_filter(self, query_filter, result_set, label=""):
        """필터 조건으로 조회하여 video ID를 result_set에 추가합니다."""
        has_more = True
        start_cursor = None
        count = 0

        while has_more:
            kwargs = {
                "database_id": self.database_id,
                "page_size": 100,
                "filter": query_filter,
            }
            if start_cursor:
                kwargs["start_cursor"] = start_cursor
            response = self.client.databases.query(**kwargs)

            for page in response.get("results", []):
                props = page.get("properties", {})
                url_prop = props.get(self.col_youtube_url, {})
                url = self._extract_url(url_prop) or self._extract_text(url_prop)
                if url:
                    vid = self._extract_video_id(url)
                    if vid:
                        result_set.add(vid)
                        count += 1

            has_more = response.get("has_more", False)
            start_cursor = response.get("next_cursor")

        if count > 0:
            console.print(f"[dim]  {label}: {count}건[/dim]")

    @staticmethod
    def _extract_video_id(url):
        """YouTube URL에서 video ID를 추출합니다."""
        if not url:
            return None
        # https://www.youtube.com/watch?v=XXXXX 또는 https://youtu.be/XXXXX
        import re
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', url)
        return match.group(1) if match else url  # ID 추출 실패 시 URL 자체 반환

    def check_duplicates(self, tasks):
        """작업 목록에서 중복 영상을 찾아 표시합니다.
        중복 기준 (2단계):
        1) 전체 DB에서 이미 댓글 완료된 영상 (체크박스/상태 기준)
        2) 대기 목록 내 같은 영상이 2건 이상 등록된 경우 (첫 번째만 유지)
        Returns:
            (clean_tasks, duplicate_tasks): 중복이 아닌 작업 / 중복 작업 리스트
        """
        completed_ids = self.get_completed_video_urls()

        clean_tasks = []
        duplicate_tasks = []
        seen_in_batch = set()  # 대기 목록 내 중복 체크

        for task in tasks:
            vid = self._extract_video_id(task.get("youtube_url", ""))
            account = task.get("account_label", "") or task.get("comment_account", "") or ""
            is_duplicate = False
            reason = ""

            # 1) 이미 댓글 완료된 영상인지 체크 (계정 무관 — 1영상 1댓글 원칙)
            if vid and vid in completed_ids:
                is_duplicate = True
                reason = "이미 댓글 완료"

            # 2) 대기 목록 내에서 같은 영상이 중복 등록된 경우 (첫 번째만 유지)
            elif vid and vid in seen_in_batch:
                is_duplicate = True
                reason = "대기 목록 내 중복"

            # 3) 계정이 미지정인 경우 경고 (중복은 아니지만 주의)
            if not account and not is_duplicate and vid:
                console.print(f"[dim]계정 미지정: {task['youtube_url'][:50]} → 첫 번째 계정 자동 배정[/dim]")

            if is_duplicate:
                duplicate_tasks.append(task)
                console.print(f"[yellow]중복 감지({reason}) → {task['youtube_url'][:60]}[/yellow]")
                try:
                    self.update_task_status(task["page_id"], "중복")
                except Exception as e:
                    console.print(f"[red]중복 상태 변경 실패: {e}[/red]")
            else:
                clean_tasks.append(task)
                if vid:
                    seen_in_batch.add(vid)

        if duplicate_tasks:
            console.print(f"[yellow]중복 영상 {len(duplicate_tasks)}건 발견 → 상태를 '중복'으로 변경[/yellow]")

        return clean_tasks, duplicate_tasks

    def update_task_error(self, page_id, error_message):
        """에러 상태를 Notion에 저장합니다."""
        self.update_task_result(page_id, comment_url="", status="에러")

    def update_task_status(self, page_id, status):
        """노션 작업의 상태만 업데이트합니다."""
        try:
            self.client.pages.update(
                page_id=page_id,
                properties={self.col_status: {"select": {"name": status}}},
            )
            console.print(f"[green]상태 업데이트: {status}[/green]")
        except Exception:
            try:
                self.client.pages.update(
                    page_id=page_id,
                    properties={self.col_status: {"status": {"name": status}}},
                )
                console.print(f"[green]상태 업데이트: {status}[/green]")
            except Exception as e:
                console.print(f"[red]상태 업데이트 실패: {e}[/red]")
                raise
