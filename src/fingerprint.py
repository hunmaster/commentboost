"""
안티디텍트 브라우저 지문(Fingerprint) 모듈

가이드라인 핵심:
- 각 브라우저 프로필마다 고유한 브라우저 지문 생성
- 계정 간 연결(linkage) 차단
- 프로필마다 다른 User-Agent, 화면 해상도, 언어, 시간대 설정

안티디텍트 브라우저(GoLogin, AdsPower 등)의 핵심 기능을
Playwright 레벨에서 구현합니다.
"""

import random
import hashlib
import json
import os
from rich.console import Console

console = Console()

# 실제 Chrome 버전별 User-Agent 목록 (Windows 10/11 기준)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]

# 일반적인 화면 해상도 목록
VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1536, "height": 864},
    {"width": 1440, "height": 900},
    {"width": 1280, "height": 720},
    {"width": 1600, "height": 900},
    {"width": 1280, "height": 800},
    {"width": 1280, "height": 1024},
]

# 한국어 로케일 변형
LOCALES = ["ko-KR", "ko", "ko-KR"]

# WebGL 렌더러 변형 (Canvas fingerprint 우회)
WEBGL_VENDORS = [
    "Google Inc. (NVIDIA)",
    "Google Inc. (AMD)",
    "Google Inc. (Intel)",
]

WEBGL_RENDERERS = [
    "ANGLE (NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (NVIDIA GeForce GTX 1660 Ti Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (AMD Radeon RX 5700 XT Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (Intel UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0)",
    "ANGLE (Intel Iris Xe Graphics Direct3D11 vs_5_0 ps_5_0)",
]


class FingerprintManager:
    """
    계정별 고유 브라우저 지문을 생성하고 관리합니다.

    안티디텍트 브라우저의 핵심 기능:
    1. 계정마다 다른 User-Agent
    2. 계정마다 다른 화면 해상도
    3. 계정마다 다른 WebGL 정보
    4. 한 계정은 항상 같은 지문 유지 (일관성)
    """

    def __init__(self):
        self.fingerprints_file = "config/fingerprints.json"
        self.fingerprints = self._load_fingerprints()

    def _load_fingerprints(self):
        """저장된 지문 데이터를 로드합니다."""
        if os.path.exists(self.fingerprints_file):
            with open(self.fingerprints_file, "r") as f:
                return json.load(f)
        return {}

    def _save_fingerprints(self):
        """지문 데이터를 저장합니다."""
        os.makedirs(os.path.dirname(self.fingerprints_file), exist_ok=True)
        with open(self.fingerprints_file, "w") as f:
            json.dump(self.fingerprints, f, indent=2, ensure_ascii=False)

    def get_fingerprint(self, account_label):
        """
        계정에 대한 고유 지문을 반환합니다.
        이미 생성된 지문이 있으면 재사용합니다 (일관성 유지).
        """
        if account_label in self.fingerprints:
            console.print(f"[blue]기존 지문 로드: {account_label}[/blue]")
            return self.fingerprints[account_label]

        # 새 지문 생성 - 계정 라벨을 시드로 사용하여 결정적 생성
        fingerprint = self._generate_fingerprint(account_label)
        self.fingerprints[account_label] = fingerprint
        self._save_fingerprints()

        console.print(f"[green]새 지문 생성: {account_label}[/green]")
        return fingerprint

    def _generate_fingerprint(self, account_label):
        """계정별 고유 지문을 생성합니다."""
        # 계정 라벨을 시드로 사용하여 결정적이면서 고유한 선택
        seed = int(hashlib.md5(account_label.encode()).hexdigest(), 16)
        rng = random.Random(seed)

        fingerprint = {
            "user_agent": rng.choice(USER_AGENTS),
            "viewport": rng.choice(VIEWPORTS),
            "locale": rng.choice(LOCALES),
            "timezone_id": "Asia/Seoul",
            "webgl_vendor": rng.choice(WEBGL_VENDORS),
            "webgl_renderer": rng.choice(WEBGL_RENDERERS),
            "platform": "Win32",
            "hardware_concurrency": rng.choice([4, 6, 8, 12, 16]),
            "device_memory": rng.choice([4, 8, 16]),
            "color_depth": 24,
            "pixel_ratio": rng.choice([1.0, 1.25, 1.5]),
        }

        return fingerprint

    def get_playwright_context_args(self, account_label):
        """Playwright 브라우저 컨텍스트 생성에 필요한 인자를 반환합니다."""
        fp = self.get_fingerprint(account_label)

        return {
            "user_agent": fp["user_agent"],
            "viewport": fp["viewport"],
            "locale": fp["locale"],
            "timezone_id": fp["timezone_id"],
            "device_scale_factor": fp["pixel_ratio"],
            "color_scheme": "light",
        }

    def get_antidetect_scripts(self, account_label):
        """
        브라우저 지문 위조를 위한 JavaScript를 반환합니다.
        Canvas, WebGL, navigator 속성 등을 오버라이드합니다.
        """
        fp = self.get_fingerprint(account_label)

        script = f"""
        // Navigator 속성 오버라이드
        Object.defineProperty(navigator, 'hardwareConcurrency', {{
            get: () => {fp['hardware_concurrency']}
        }});
        Object.defineProperty(navigator, 'deviceMemory', {{
            get: () => {fp['device_memory']}
        }});
        Object.defineProperty(navigator, 'platform', {{
            get: () => '{fp['platform']}'
        }});

        // WebGL 벤더/렌더러 오버라이드
        const getParameterOrig = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {{
            if (parameter === 37445) return '{fp['webgl_vendor']}';
            if (parameter === 37446) return '{fp['webgl_renderer']}';
            return getParameterOrig.call(this, parameter);
        }};

        // WebDriver 탐지 방지
        Object.defineProperty(navigator, 'webdriver', {{
            get: () => undefined
        }});

        // Chrome 객체 존재 확인 (자동화 탐지 방지)
        if (!window.chrome) {{
            window.chrome = {{
                runtime: {{}},
                loadTimes: function() {{}},
                csi: function() {{}},
                app: {{}}
            }};
        }}

        // Permissions 탐지 방지
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
            Promise.resolve({{ state: Notification.permission }}) :
            originalQuery(parameters)
        );
        """

        return script
