"""
댓글 부스터 - 메인 오케스트레이터

프로세스:
1. 노션 DB에서 대기 중인 작업 목록 가져오기
2. 안전 규칙 검사 (1일 제한, 시간 간격, 링크 차단, 유사 문구 차단)
3. 각 작업에 대해:
   a. 안티디텍트 지문 설정 (계정별 고유 fingerprint)
   b. 해당 계정의 프록시 설정 (IP 가이드라인)
   c. 시크릿 모드 브라우저 시작
   d. YouTube 로그인
   e. 댓글 작성
   f. 댓글 URL 추출
   g. 노션 DB에 결과 저장
   h. SMM Kings API로 댓글 좋아요 구매
   i. 안전 규칙 기록 (히스토리 저장)
   j. 브라우저 완전 종료 (세션 초기화)
   k. IP 변경 대기

운영 전략:
- 1계정당 20개 댓글, 시간 간격을 두고 작업
- 계정 전환 시 IP 변경 (프록시 로테이션)
- 댓글 작성 후 자동으로 좋아요 구매
"""

import os
import sys
import json
import time

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notion_client import NotionManager
from src.youtube_bot import YouTubeBot
from src.fingerprint import FingerprintManager
from src.safety_rules import SafetyRules
from src.smm_client import SMMClient
from src.adb_ip_changer import ADBIPChanger

console = Console()


def load_accounts():
    """계정 정보를 JSON 파일에서 로드합니다."""
    accounts_file = os.getenv("ACCOUNTS_FILE", "config/accounts.json")

    if not os.path.exists(accounts_file):
        console.print(f"[red]계정 파일을 찾을 수 없습니다: {accounts_file}[/red]")
        console.print("[yellow]config/accounts.example.json을 참고하여 accounts.json을 생성하세요.[/yellow]")
        return []

    with open(accounts_file, "r") as f:
        accounts = json.load(f)

    console.print(f"[green]계정 {len(accounts)}개 로드됨[/green]")
    return accounts


def find_account(accounts, account_label):
    """라벨로 계정을 찾습니다."""
    if not account_label:
        return accounts[0] if accounts else None

    for acc in accounts:
        if acc.get("label") == account_label:
            return acc

    for acc in accounts:
        if acc.get("email", "").startswith(account_label):
            return acc

    return accounts[0] if accounts else None


def display_status(tasks, safety_rules, accounts, smm_client):
    """작업 현황을 표시합니다."""
    console.print(Panel("[bold]댓글 부스터[/bold]", style="blue"))

    # 작업 목록 테이블
    table = Table(title="작업 목록")
    table.add_column("번호", style="cyan", width=5)
    table.add_column("유튜브 URL", style="blue", max_width=50)
    table.add_column("댓글 미리보기", style="white", max_width=30)
    table.add_column("계정", style="green", width=10)

    for i, task in enumerate(tasks, 1):
        comment_preview = (
            task["comment_text"][:30] + "..."
            if len(task["comment_text"]) > 30
            else task["comment_text"]
        )
        table.add_row(
            str(i),
            task["youtube_url"][:50],
            comment_preview,
            task.get("account", "-"),
        )

    console.print(table)

    # 계정별 오늘 사용 현황
    account_table = Table(title="계정별 오늘 댓글 현황")
    account_table.add_column("계정", style="cyan")
    account_table.add_column("오늘 사용", style="yellow")
    account_table.add_column("남은 횟수", style="green")

    shown_accounts = set()
    for task in tasks:
        acc = find_account(accounts, task.get("account"))
        if acc:
            label = acc.get("label", acc.get("email", "unknown"))
            if label not in shown_accounts:
                shown_accounts.add(label)
                status = safety_rules.get_account_status(label)
                account_table.add_row(
                    label,
                    f"{status['today_count']}/{status['max_count']}",
                    str(status["remaining"]),
                )

    console.print(account_table)

    # SMM 잔액 표시
    if smm_client.enabled:
        smm_client.get_balance()
    else:
        console.print("[dim]SMM 좋아요 구매: 비활성[/dim]")


def process_task(task, account, fingerprint_manager, safety_rules):
    """
    개별 댓글 작업을 처리합니다.

    Returns:
        (comment_url or "SKIP" or None, error_msg or None)
    """
    account_label = account.get("label", account.get("email", "unknown"))
    console.print(f"\n[bold blue]━━━ 작업 시작: {account_label} ━━━[/bold blue]")

    # 1. 안전 규칙 검사
    passed, reason = safety_rules.check_all_rules(
        account_label, task["youtube_url"], task["comment_text"]
    )
    if not passed:
        console.print(f"[red]안전 규칙 위반: {reason}[/red]")
        return "SKIP", reason

    # 2. 안티디텍트 지문이 적용된 시크릿 모드 브라우저 시작
    bot = YouTubeBot(
        fingerprint_manager=fingerprint_manager,
        account_label=account_label,
    )
    try:
        bot.start_browser()

        # 4. YouTube 로그인
        login_success = bot.login_youtube(account["email"], account["password"])
        if not login_success:
            console.print("[red]로그인 실패 - 다음 작업으로 넘어갑니다[/red]")
            return None, "로그인 실패"

        # 5. 댓글 작성 및 URL 추출 (인간형 랜덤 타이핑 속도)
        human_delay = safety_rules.get_human_delay("comment")
        comment_url = bot.post_comment(
            task["youtube_url"],
            task["comment_text"],
            typing_delay_ms=human_delay["typing_delay_ms"],
        )

        if comment_url:
            # 6. 안전 규칙 히스토리 기록
            safety_rules.record_comment(
                account_label, task["youtube_url"], task["comment_text"]
            )

        return comment_url, None

    except Exception as e:
        console.print(f"[red]작업 중 오류 발생: {e}[/red]")
        return None, str(e)

    finally:
        # 7. 브라우저 완전 종료 (IP 가이드라인: 모든 창 닫기)
        bot.close_browser()


def run():
    """메인 실행 함수"""
    # 환경 변수 로드
    load_dotenv()

    console.print(Panel(
        "[bold green]댓글 부스터 시작[/bold green]\n"
        "IP 가이드라인 + 유튜브 바이럴 가이드라인 적용\n"
        "안티디텍트 지문 | 프록시 로테이션 | 댓글 안전 규칙 | 좋아요 자동 구매",
        style="green",
    ))

    # 모듈 초기화
    try:
        notion = NotionManager()
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        return

    fingerprint_manager = FingerprintManager()
    safety_rules = SafetyRules()
    smm_client = SMMClient()
    accounts = load_accounts()

    if not accounts:
        console.print("[red]사용 가능한 계정이 없습니다. config/accounts.json을 확인해주세요.[/red]")
        return

    # 대기 중인 작업 가져오기
    tasks = notion.get_pending_tasks()
    if not tasks:
        console.print("[yellow]대기 중인 작업이 없습니다.[/yellow]")
        return

    # 중복 영상 체크
    console.print("[blue]중복 영상 체크 중...[/blue]")
    tasks, duplicate_tasks = notion.check_duplicates(tasks)
    if duplicate_tasks:
        console.print(f"[yellow]중복 영상 {len(duplicate_tasks)}건 → '중복' 상태로 변경됨[/yellow]")
    if not tasks:
        console.print("[yellow]중복 제외 후 대기 작업이 없습니다.[/yellow]")
        return

    # 현황 표시
    display_status(tasks, safety_rules, accounts, smm_client)

    # ADB IP 변경 활성 시 유선 인터넷 비활성화
    adb_changer = ADBIPChanger()
    _ethernet_disabled = False
    if adb_changer.enabled and adb_changer.auto_ethernet:
        console.print("[yellow]유선 인터넷 비활성화 → USB 테더링으로 전환 중...[/yellow]")
        ok, msg = adb_changer.disable_ethernet()
        if ok:
            _ethernet_disabled = True
            console.print(f"[green]{msg}[/green]")
        else:
            console.print(f"[red]유선 비활성화 실패: {msg}[/red]")

    # 작업 실행
    delay_ip_change = int(os.getenv("DELAY_AFTER_IP_CHANGE", "3"))
    comment_interval = int(os.getenv("COMMENT_INTERVAL_SEC", "180"))
    success_count = 0
    fail_count = 0
    skip_count = 0
    successful_comment_urls = []  # 대량 좋아요 주문용 URL 수집
    prev_account_label = None

    for i, task in enumerate(tasks, 1):
        console.print(f"\n[bold]═══ 작업 {i}/{len(tasks)} ═══[/bold]")

        # 사용할 계정 결정
        account = find_account(accounts, task.get("account"))
        if not account:
            console.print("[red]사용 가능한 계정이 없습니다.[/red]")
            notion.update_task_error(task["page_id"], "계정 없음")
            fail_count += 1
            continue

        current_label = account.get("label", account.get("email"))

        # 계정 전환 시 IP 변경 (ADB 비행기모드 or 대기)
        if prev_account_label and prev_account_label != current_label:
            console.print(
                f"[yellow]계정 전환: {prev_account_label} → {current_label}[/yellow]"
            )
            adb_changer = ADBIPChanger()
            if adb_changer.enabled:
                old_ip = adb_changer.get_current_ip()
                console.print(f"[yellow]ADB 비행기모드로 IP 변경 중... (현재 IP: {old_ip or '확인불가'})[/yellow]")
                success, msg = adb_changer.toggle_airplane_mode()
                new_ip = adb_changer.get_current_ip()
                if success:
                    console.print(f"[green]IP 변경 완료: {old_ip or '?'} → {new_ip or '?'}[/green]")
                else:
                    console.print(f"[red]IP 변경 실패: {msg} | IP: {new_ip or '확인불가'} (계속 진행)[/red]")
            else:
                console.print(
                    f"[yellow]IP 변경 대기 중... ({delay_ip_change}초)[/yellow]"
                )
                time.sleep(delay_ip_change)
        elif prev_account_label == current_label and i > 1:
            # 같은 계정 연속 사용 시 인간형 랜덤 간격 대기
            human_delay = safety_rules.get_human_delay("comment")
            actual_delay = human_delay["delay_sec"]
            console.print(
                f"[yellow]같은 계정 연속 사용 - "
                f"🧑 {human_delay['description']} "
                f"(타이핑 {human_delay['typing_delay_ms']}ms)[/yellow]"
            )
            time.sleep(actual_delay)

        # 작업 실행
        result, error_msg = process_task(
            task, account, fingerprint_manager, safety_rules
        )

        if result == "SKIP":
            console.print(f"[yellow]작업 {i} 건너뜀: {error_msg}[/yellow]")
            skip_count += 1
        elif result:
            comment_url = result if isinstance(result, str) else ""
            notion.update_task_result(task["page_id"], comment_url, status="댓글완료")
            success_count += 1
            console.print(f"[green]작업 {i} 완료[/green]")
            if comment_url:
                successful_comment_urls.append(comment_url)
        else:
            notion.update_task_error(task["page_id"], error_msg or "댓글 작성 실패")
            fail_count += 1
            console.print(f"[red]작업 {i} 실패[/red]")

        prev_account_label = current_label

    # SMM 대량 좋아요 주문 (모든 댓글 완료 후 한 번에)
    like_order_count = 0
    if smm_client.enabled and successful_comment_urls:
        console.print(f"\n[bold blue]━━━ SMM 대량 좋아요 주문 ({len(successful_comment_urls)}건) ━━━[/bold blue]")
        mass_result = smm_client.order_mass_likes(successful_comment_urls)
        if mass_result["success"]:
            like_order_count = len(mass_result["order_ids"])
            # 주문 상태 확인
            if mass_result["order_ids"]:
                smm_client.check_multiple_orders(mass_result["order_ids"])
        if mass_result.get("errors"):
            for err in mass_result["errors"]:
                console.print(f"[yellow]좋아요 주문 오류: {err}[/yellow]")

    # 결과 요약
    summary_lines = [
        f"[bold]작업 완료![/bold]",
        f"댓글 성공: [green]{success_count}[/green]건",
        f"댓글 실패: [red]{fail_count}[/red]건",
        f"건너뜀: [yellow]{skip_count}[/yellow]건",
        f"전체: {len(tasks)}건",
    ]
    if smm_client.enabled:
        summary_lines.append(f"좋아요 대량주문: [blue]{like_order_count}[/blue]건")

    # 유선 인터넷 복원
    if _ethernet_disabled:
        console.print("[yellow]유선 인터넷 복원 중...[/yellow]")
        ok, msg = adb_changer.enable_ethernet()
        if ok:
            console.print(f"[green]{msg}[/green]")
        else:
            console.print(f"[red]유선 복원 실패: {msg}[/red]")

    console.print(Panel(
        "\n".join(summary_lines),
        style="blue",
        title="결과 요약",
    ))


if __name__ == "__main__":
    run()
