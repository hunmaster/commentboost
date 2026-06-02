"""
ADB를 이용한 IP 변경 모듈

모바일 USB 테더링 환경에서 비행기모드 ON→OFF로 LTE IP를 변경합니다.
- 계정 전환 시 자동 호출
- 1계정 = 1IP 원칙 준수
- 자동화 시작/종료 시 유선 인터넷 비활성화/활성화
"""

import os
import subprocess
import time
import re
import platform

from rich.console import Console

console = Console()


class ADBIPChanger:
    # 클래스 변수: 모든 인스턴스에서 공유 (인스턴스 새로 만들어도 유지)
    _tether_interface_ip = None

    def __init__(self):
        self.adb_path = os.getenv("ADB_PATH", "adb")
        self.airplane_wait = int(os.getenv("ADB_AIRPLANE_WAIT", "4"))
        self.enabled = os.getenv("ADB_IP_CHANGE_ENABLED", "false").lower() == "true"
        self.ethernet_name = os.getenv("ADB_ETHERNET_NAME", "이더넷")
        self.auto_ethernet = os.getenv("ADB_AUTO_ETHERNET", "true").lower() == "true"

    def _get_startupinfo(self):
        """Windows에서 CMD 창이 절대 뜨지 않도록 startupinfo를 반환합니다."""
        if platform.system() == "Windows":
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            si.wShowWindow = 0  # SW_HIDE
            return si
        return None

    def _get_creation_flags(self):
        """Windows에서 CMD 창이 뜨지 않도록 플래그를 반환합니다."""
        if platform.system() == "Windows":
            return subprocess.CREATE_NO_WINDOW
        return 0

    def _run_adb(self, *args):
        """ADB 명령어를 실행하고 결과를 반환합니다."""
        cmd = [self.adb_path] + list(args)
        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=15,
                creationflags=self._get_creation_flags(),
                startupinfo=self._get_startupinfo(),
                encoding="utf-8", errors="replace",
            )
            return result.stdout.strip(), result.returncode
        except FileNotFoundError:
            console.print(f"[red]ADB를 찾을 수 없습니다: {self.adb_path}[/red]")
            return "", 1
        except subprocess.TimeoutExpired:
            console.print("[red]ADB 명령 시간 초과[/red]")
            return "", 1

    def _run_cmd(self, cmd_str):
        """시스템 명령어를 실행합니다 (유선 인터넷 제어용).
        CMD 창이 절대 뜨지 않도록 shell=False + startupinfo 사용.
        """
        try:
            # 문자열을 리스트로 변환 (따옴표 내부는 분리하지 않음)
            import shlex
            if isinstance(cmd_str, str):
                # Windows에서 shlex.split은 posix=False로 사용
                try:
                    args = shlex.split(cmd_str, posix=False)
                    # 따옴표 제거
                    args = [a.strip('"').strip("'") for a in args]
                except ValueError:
                    args = cmd_str.split()
            else:
                args = cmd_str
            result = subprocess.run(
                args, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=self._get_creation_flags(),
                startupinfo=self._get_startupinfo(),
            )
            return "", result.returncode
        except Exception as e:
            console.print(f"[red]명령 실행 실패: {e}[/red]")
            return "", 1

    def check_device(self):
        """연결된 ADB 디바이스를 확인합니다."""
        output, code = self._run_adb("devices")
        if code != 0:
            return False, "ADB 실행 실패"

        lines = output.strip().split("\n")
        devices = []
        for line in lines[1:]:
            parts = line.strip().split("\t")
            if len(parts) == 2:
                serial, status = parts
                devices.append({"serial": serial, "status": status})

        if not devices:
            return False, "연결된 디바이스 없음"

        for d in devices:
            if d["status"] == "device":
                console.print(f"[green]ADB 디바이스 연결됨: {d['serial']}[/green]")
                return True, d["serial"]
            elif d["status"] == "unauthorized":
                return False, f"디바이스 인증 필요 (USB 디버깅 허용): {d['serial']}"

        return False, f"디바이스 상태 이상: {devices}"

    def get_current_ip(self, use_tethering=False):
        """현재 외부 IP를 확인합니다.
        use_tethering=True이면 USB 테더링 인터페이스 경유로만 확인합니다.
        """
        interface_ip = None
        if use_tethering:
            interface_ip = self._get_tethering_local_ip()

        apis = [
            "https://api.ipify.org",
            "https://checkip.amazonaws.com",
            "https://ifconfig.me/ip",
        ]
        for api_url in apis:
            try:
                cmd = ["curl", "-s", "--max-time", "10"]
                if interface_ip:
                    cmd += ["--interface", interface_ip]
                cmd.append(api_url)
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=15,
                    creationflags=self._get_creation_flags(),
                    startupinfo=self._get_startupinfo(),
                )
                ip = result.stdout.strip()
                if result.returncode == 0 and re.match(r"\d+\.\d+\.\d+\.\d+", ip):
                    return ip
            except Exception:
                continue
        return None

    def _get_tethering_local_ip(self):
        """USB 테더링 어댑터의 로컬 IPv4 주소를 가져옵니다.
        PowerShell로 RNDIS/Android 어댑터의 IP를 조회합니다."""
        if platform.system() != "Windows":
            return None
        try:
            ps_script = (
                "Get-NetAdapter | Where-Object {$_.Status -eq 'Up' -and $_.InterfaceDescription -match 'RNDIS|Remote NDIS|Android|USB'} "
                "| Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue "
                "| Select-Object -First 1 -ExpandProperty IPAddress"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                capture_output=True, timeout=10,
                creationflags=self._get_creation_flags(),
                startupinfo=self._get_startupinfo(),
            )
            for enc in ["utf-8", "cp949"]:
                try:
                    ip = result.stdout.decode(enc).strip()
                    if re.match(r"\d+\.\d+\.\d+\.\d+", ip):
                        return ip
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def _get_ip_via_interface(self):
        """(레거시 호환용) USB 테더링 경유 외부 IP."""
        local_ip = self._get_tethering_local_ip()
        if not local_ip:
            return None
        try:
            result = subprocess.run(
                ["curl", "-s", "--max-time", "10",
                 "--interface", local_ip,
                 "https://api.ipify.org"],
                capture_output=True, text=True, timeout=10,
                creationflags=self._get_creation_flags(),
                startupinfo=self._get_startupinfo(),
            )
            if result.returncode == 0 and re.match(r"\d+\.\d+\.\d+\.\d+", result.stdout.strip()):
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _find_ethernet_adapters(self):
        """Windows에서 활성 유선 이더넷 어댑터 목록을 가져옵니다.
        USB 테더링(RNDIS/Android) 어댑터는 제외합니다.
        netsh 한국어 인코딩 파싱 문제 → PowerShell로 어댑터 목록 조회.
        """
        adapters = []
        try:
            # Name과 InterfaceDescription을 함께 가져와서 USB 테더링 구분
            ps_script = (
                "Get-NetAdapter | Where-Object {$_.Status -eq 'Up' -and $_.InterfaceDescription -notmatch 'Wi-Fi|Bluetooth|Loopback|Virtual'} "
                "| ForEach-Object { $_.Name + '|||' + $_.InterfaceDescription }"
            )
            result = subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                capture_output=True, timeout=10,
                creationflags=self._get_creation_flags(),
                startupinfo=self._get_startupinfo(),
            )
            text = ""
            for enc in ["utf-8", "cp949", "euc-kr"]:
                try:
                    text = result.stdout.decode(enc).strip()
                    if text:
                        break
                except Exception:
                    continue
            if text:
                for line in text.split('\n'):
                    line = line.strip()
                    if '|||' in line:
                        name, desc = line.split('|||', 1)
                        name = name.strip()
                        desc = desc.strip().lower()
                        # USB 테더링 어댑터 제외 (RNDIS, Android, Remote NDIS 등)
                        if any(usb_kw in desc for usb_kw in ['rndis', 'remote ndis', 'android', 'usb ethernet', 'usb net']):
                            console.print(f"[dim]USB 테더링 어댑터 제외: {name} ({desc})[/dim]")
                            continue
                        if name and name not in adapters:
                            adapters.append(name)
                    elif line:
                        # 구분자 없으면 이름만 (fallback)
                        if line not in adapters:
                            adapters.append(line)
        except Exception:
            pass
        return adapters

    def disable_ethernet(self):
        """USB 테더링 인터페이스를 감지하고 바인딩합니다.
        네트워크 설정을 일절 변경하지 않습니다 (netsh 호출 없음).
        curl --interface로 USB 테더링 경유 IP만 확인합니다.
        CMD 창, 네트워크 끊김 모두 없습니다.
        """
        if not self.auto_ethernet:
            return True, "유선 자동 제어 비활성"

        if platform.system() != "Windows":
            return False, "Windows 전용 기능"

        # USB 테더링 로컬 IP 찾기
        tether_local_ip = self._get_tethering_local_ip()
        if not tether_local_ip:
            return False, "USB 테더링을 찾을 수 없습니다. 폰의 USB 테더링을 켜주세요."

        ADBIPChanger._tether_interface_ip = tether_local_ip

        # USB 테더링 경유 외부 IP 확인
        tether_external_ip = self.get_current_ip(use_tethering=True)
        if not tether_external_ip:
            return False, "USB 테더링은 감지되었지만 인터넷 연결을 확인할 수 없습니다."

        console.print(f"[green]USB 테더링 감지 완료: {tether_local_ip} → 외부 IP: {tether_external_ip} (네트워크 변경 없음)[/green]")
        return True, f"USB 테더링 바인딩 완료 (IP: {tether_external_ip}, 네트워크 변경 없음)"

    def enable_ethernet(self):
        """USB 테더링 바인딩을 해제합니다.
        네트워크 설정을 변경하지 않으므로 복원할 것도 없습니다.
        """
        ADBIPChanger._tether_interface_ip = None
        console.print("[green]USB 테더링 바인딩 해제 완료[/green]")
        return True, "USB 테더링 바인딩 해제 완료"

    def _check_airplane_status(self):
        """현재 비행기모드 상태를 확인합니다. True=ON, False=OFF, None=확인불가"""
        output, code = self._run_adb("shell", "settings", "get", "global", "airplane_mode_on")
        if code == 0:
            val = output.strip()
            return val == "1"
        return None

    def toggle_airplane_mode(self):
        """
        비행기모드 ON → OFF로 IP를 변경합니다.
        여러 방식 시도: cmd connectivity → settings put → svc data

        Returns:
            (bool, str): (성공여부, 메시지)
        """
        if not self.enabled:
            return False, "ADB IP 변경이 비활성화되어 있습니다"

        # 디바이스 확인
        connected, info = self.check_device()
        if not connected:
            return False, f"ADB 디바이스 연결 실패: {info}"

        # USB 테더링 바인딩이 있으면 테더링 경유로 IP 확인 (클래스 변수)
        _use_tether = bool(ADBIPChanger._tether_interface_ip)
        old_ip = self.get_current_ip(use_tethering=_use_tether)
        console.print(f"[blue]현재 IP: {old_ip or '확인 불가'}{' (USB 테더링 경유)' if _use_tether else ''}[/blue]")

        # 비행기모드 상태 확인
        before_status = self._check_airplane_status()
        console.print(f"[dim]비행기모드 상태 (전): {'ON' if before_status else 'OFF' if before_status is not None else '확인불가'}[/dim]")

        # 방법 1: cmd connectivity (최신 Android/Samsung)
        console.print("[yellow]비행기모드 ON (cmd connectivity)...[/yellow]")
        self._run_adb("shell", "cmd connectivity airplane-mode enable")
        time.sleep(1)

        # 비행기모드 ON 확인
        after_on = self._check_airplane_status()
        if not after_on:
            # 방법 2: settings put + broadcast (구형 Android)
            console.print("[yellow]cmd connectivity 실패 → settings put 방식 시도...[/yellow]")
            self._run_adb("shell", "settings", "put", "global", "airplane_mode_on", "1")
            self._run_adb("shell", "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE", "--ez", "state", "true")
            time.sleep(1)
            after_on = self._check_airplane_status()

        if not after_on:
            # 방법 3: svc data off (데이터만 끄기)
            console.print("[yellow]비행기모드 토글 실패 → 모바일 데이터 OFF/ON 방식 시도...[/yellow]")
            self._run_adb("shell", "svc data disable")
            time.sleep(self.airplane_wait)
            self._run_adb("shell", "svc data enable")
        else:
            console.print(f"[green]비행기모드 ON 확인됨[/green]")
            time.sleep(self.airplane_wait)

            # 비행기모드 OFF
            console.print("[yellow]비행기모드 OFF...[/yellow]")
            self._run_adb("shell", "cmd connectivity airplane-mode disable")
            time.sleep(1)
            after_off = self._check_airplane_status()
            if after_off:
                # 방법 2로 OFF
                self._run_adb("shell", "settings", "put", "global", "airplane_mode_on", "0")
                self._run_adb("shell", "am", "broadcast", "-a", "android.intent.action.AIRPLANE_MODE", "--ez", "state", "false")

        # 네트워크 재연결 대기 (확인 기반 — old_ip와 다른 IP 올 때까지)
        console.print("[yellow]네트워크 재연결 대기 중...[/yellow]")
        new_ip = self._wait_for_network(max_wait=20, old_ip=old_ip)

        if old_ip and new_ip and old_ip != new_ip:
            console.print(f"[green]IP 변경 성공: {old_ip} → {new_ip}[/green]")
            return True, f"{old_ip} → {new_ip}"
        elif old_ip and new_ip and old_ip == new_ip:
            # IP가 변경되지 않음 — 재시도
            console.print(f"[yellow]IP 미변경 ({new_ip}) — 비행기모드 재시도...[/yellow]")
            self._run_adb("shell", "cmd connectivity airplane-mode enable")
            time.sleep(self.airplane_wait)
            self._run_adb("shell", "cmd connectivity airplane-mode disable")
            retry_ip = self._wait_for_network(max_wait=10)
            if retry_ip and retry_ip != old_ip:
                console.print(f"[green]재시도 IP 변경 성공: {old_ip} → {retry_ip}[/green]")
                return True, f"{old_ip} → {retry_ip}"
            else:
                console.print(f"[red]IP 변경 실패 — 동일 IP 유지: {retry_ip or new_ip}[/red]")
                return False, f"IP 미변경 ({retry_ip or new_ip}) — 동일 IP로 계속 진행"
        elif new_ip:
            console.print(f"[yellow]이전 IP 확인 불가 (현재 IP: {new_ip})[/yellow]")
            return True, f"비행기모드 토글 완료 (IP: {new_ip})"
        else:
            console.print("[red]IP 확인 불가[/red]")
            return False, "네트워크 복구 실패 — IP 확인 불가"

    def _wait_for_network(self, max_wait=15, old_ip=None):
        """네트워크가 복구될 때까지 대기합니다. IP를 반환합니다.
        old_ip가 주어지면 다른 IP가 올 때까지 추가 대기합니다.
        """
        _use_tether = bool(ADBIPChanger._tether_interface_ip)
        start = time.time()
        attempt = 0
        first_ip = None
        while time.time() - start < max_wait:
            attempt += 1
            ip = self.get_current_ip(use_tethering=_use_tether)
            if ip:
                if not first_ip:
                    first_ip = ip
                # old_ip가 주어지고 아직 같은 IP면 → 추가 대기 (IP 변경 확인)
                if old_ip and ip == old_ip and time.time() - start < max_wait - 2:
                    console.print(f"[yellow]  IP 아직 동일 ({ip}) — 변경 대기 중... ({attempt}회)[/yellow]")
                    time.sleep(2)
                    continue
                elapsed = time.time() - start
                console.print(f"[green]네트워크 복구 확인 ({elapsed:.1f}초, {attempt}회 시도) IP: {ip}[/green]")
                return ip
            wait_sec = min(2, 0.5 * (2 ** attempt))  # 0.5, 1, 2, 2...
            console.print(f"[yellow]  네트워크 대기 중... ({attempt}회, {wait_sec:.1f}초 후 재시도)[/yellow]")
            time.sleep(wait_sec)
        console.print(f"[red]네트워크 복구 타임아웃 ({max_wait}초)[/red]")
        return first_ip  # 타임아웃이라도 받은 IP가 있으면 반환

    def force_airplane_off(self):
        """비행기모드를 강제로 OFF합니다 (중지 시 안전 보장)."""
        console.print("[yellow]비행기모드 강제 OFF...[/yellow]")
        self._run_adb("shell", "cmd connectivity airplane-mode disable")
        # 네트워크 복구 대기
        ip = self._wait_for_network(max_wait=10)
        if ip:
            console.print(f"[green]비행기모드 OFF 완료, IP: {ip}[/green]")
            return True, f"비행기모드 OFF, IP: {ip}"
        else:
            console.print("[yellow]비행기모드 OFF 완료 (IP 확인 불가)[/yellow]")
            return True, "비행기모드 OFF (IP 확인 불가)"

    def get_status(self):
        """ADB IP 변경 모듈 상태를 반환합니다."""
        if not self.enabled:
            return {"enabled": False, "device": None, "ip": None}

        connected, info = self.check_device()
        ip = self.get_current_ip() if connected else None
        return {
            "enabled": True,
            "device": info if connected else None,
            "device_connected": connected,
            "device_message": info,
            "ip": ip,
        }
