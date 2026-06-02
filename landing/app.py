"""CommentBoost 랜딩 페이지 서버."""
import os
import json
from flask import Flask, render_template, send_from_directory, jsonify, request

app = Flask(__name__)

# 버전 정보 로드
_version_file = os.path.join(os.path.dirname(__file__), "version.json")


def _load_version():
    """서버의 최신 버전 정보를 로드"""
    try:
        with open(_version_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": "0.0.0", "build": 0}


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/guide")
def guide():
    return render_template("guide.html")


@app.route("/api/version/check", methods=["GET", "POST"])
def api_version_check():
    """
    클라이언트 버전 체크 API.
    GET: 최신 버전 정보 반환
    POST: 클라이언트 버전과 비교하여 업데이트 필요 여부 반환
    """
    server_ver = _load_version()

    if request.method == "GET":
        return jsonify({
            "latest_version": server_ver.get("version", "0.0.0"),
            "build": server_ver.get("build", 0),
            "release_date": server_ver.get("release_date", ""),
            "changelog": server_ver.get("changelog", ""),
            "download_url": server_ver.get("download_url", ""),
            "min_version": server_ver.get("min_version", "0.0.0"),
        })

    # POST: 클라이언트 버전 비교
    data = request.get_json() or {}
    client_version = data.get("version", "0.0.0")
    client_build = data.get("build", 0)

    latest_version = server_ver.get("version", "0.0.0")
    latest_build = server_ver.get("build", 0)
    min_version = server_ver.get("min_version", "0.0.0")

    needs_update = _compare_versions(client_version, latest_version) < 0
    force_update = _compare_versions(client_version, min_version) < 0

    return jsonify({
        "needs_update": needs_update,
        "force_update": force_update,
        "latest_version": latest_version,
        "latest_build": latest_build,
        "client_version": client_version,
        "release_date": server_ver.get("release_date", ""),
        "changelog": server_ver.get("changelog", ""),
        "download_url": server_ver.get("download_url", ""),
    })


def _compare_versions(v1, v2):
    """시맨틱 버전 비교. v1 < v2 → -1, v1 == v2 → 0, v1 > v2 → 1"""
    try:
        parts1 = [int(x) for x in v1.split(".")]
        parts2 = [int(x) for x in v2.split(".")]
        # 길이 맞추기
        while len(parts1) < 3:
            parts1.append(0)
        while len(parts2) < 3:
            parts2.append(0)
        for a, b in zip(parts1, parts2):
            if a < b:
                return -1
            if a > b:
                return 1
        return 0
    except (ValueError, AttributeError):
        return 0


@app.route("/download/<path:filename>")
def download(filename):
    """다운로드 파일 서빙 (releases/ 디렉토리)."""
    releases_dir = os.path.join(os.path.dirname(__file__), "releases")
    return send_from_directory(releases_dir, filename, as_attachment=True)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
