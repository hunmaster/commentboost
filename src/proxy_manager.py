"""
프록시 관리 모듈
- IP 가이드라인에 따른 프록시 로테이션
- 부계정 작업 시 계정마다 다른 IP 사용
- 계정 전환 시 IP 변경 (비행기모드 ON/OFF 시뮬레이션)

IP 관리 규칙:
1. 부계정 30개 → 계정마다 다른 프록시 필수
2. 같은 IP에서 부계정 여러 개 → 전부 정지 위험
3. 한 계정이 IP를 자주 바꾸면 안됨 → 계정-프록시 매핑 유지
"""

import os
import random
from rich.console import Console

console = Console()


class ProxyManager:
    def __init__(self):
        self.use_proxy = os.getenv("USE_PROXY", "false").lower() == "true"
        self.rotation_mode = os.getenv("PROXY_ROTATION", "sequential")
        self.proxies = []
        self.current_index = 0
        # 계정별 프록시 매핑 (한 계정은 가능하면 같은 IP에서)
        self.account_proxy_map = {}

        if self.use_proxy:
            self._load_proxies()

    def _load_proxies(self):
        """프록시 목록 파일에서 프록시를 로드합니다."""
        proxy_file = os.getenv("PROXY_LIST_FILE", "config/proxies.txt")

        if not os.path.exists(proxy_file):
            console.print(f"[red]프록시 파일을 찾을 수 없습니다: {proxy_file}[/red]")
            console.print("[yellow]config/proxies.example.txt를 참고하여 proxies.txt를 생성하세요.[/yellow]")
            self.use_proxy = False
            return

        with open(proxy_file, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    self.proxies.append(line)

        if not self.proxies:
            console.print("[red]프록시 목록이 비어있습니다.[/red]")
            self.use_proxy = False
            return

        console.print(f"[green]프록시 {len(self.proxies)}개 로드됨[/green]")

    def get_proxy_for_account(self, account_label):
        """
        계정에 맞는 프록시를 반환합니다.

        IP 가이드라인 핵심:
        - 한 계정은 가능하면 같은 IP에서만 사용
        - 다른 계정은 반드시 다른 IP 사용
        """
        if not self.use_proxy or not self.proxies:
            return None

        # 이미 매핑된 프록시가 있으면 같은 프록시 반환
        if account_label in self.account_proxy_map:
            proxy = self.account_proxy_map[account_label]
            console.print(f"[blue]계정 '{account_label}' → 기존 프록시 사용: {self._mask_proxy(proxy)}[/blue]")
            return proxy

        # 새 프록시 할당
        proxy = self._get_next_proxy()

        # 이미 다른 계정이 사용 중인 프록시인지 확인
        used_proxies = set(self.account_proxy_map.values())
        attempts = 0
        while proxy in used_proxies and attempts < len(self.proxies):
            proxy = self._get_next_proxy()
            attempts += 1

        if proxy in used_proxies:
            console.print(
                "[yellow]경고: 사용 가능한 프록시가 부족합니다. "
                "프록시를 추가하거나 부계정 수를 줄여주세요.[/yellow]"
            )

        self.account_proxy_map[account_label] = proxy
        console.print(f"[green]계정 '{account_label}' → 새 프록시 할당: {self._mask_proxy(proxy)}[/green]")
        return proxy

    def _get_next_proxy(self):
        """로테이션 방식에 따라 다음 프록시를 반환합니다."""
        if self.rotation_mode == "random":
            return random.choice(self.proxies)

        # sequential (순차)
        proxy = self.proxies[self.current_index % len(self.proxies)]
        self.current_index += 1
        return proxy

    def _mask_proxy(self, proxy):
        """프록시 주소를 마스킹하여 로그에 안전하게 출력합니다."""
        if "@" in proxy:
            # user:pass@host:port → ***@host:port
            parts = proxy.split("@")
            return f"***@{parts[-1]}"
        return proxy

    def parse_proxy_for_playwright(self, proxy_url):
        """
        프록시 URL을 Playwright 형식으로 변환합니다.

        입력: http://user:pass@host:port 또는 http://host:port
        출력: {"server": "http://host:port", "username": "user", "password": "pass"}
        """
        if not proxy_url:
            return None

        result = {"server": proxy_url}

        if "@" in proxy_url:
            # 프로토콜 분리
            protocol = ""
            url_part = proxy_url
            if "://" in proxy_url:
                protocol, url_part = proxy_url.split("://", 1)
                protocol += "://"

            # user:pass@host:port 분리
            auth_part, host_part = url_part.rsplit("@", 1)
            if ":" in auth_part:
                username, password = auth_part.split(":", 1)
                result["server"] = f"{protocol}{host_part}"
                result["username"] = username
                result["password"] = password

        return result

    def get_status(self):
        """현재 프록시 상태를 반환합니다."""
        if not self.use_proxy:
            return "프록시 미사용 (직접 연결)"

        return (
            f"프록시 {len(self.proxies)}개 로드, "
            f"{len(self.account_proxy_map)}개 계정 매핑됨, "
            f"로테이션: {self.rotation_mode}"
        )
