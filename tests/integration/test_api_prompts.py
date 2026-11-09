"""
Prompt 管理路由集成测试

覆盖：路径穿越防护（resolve + containment）、正常的读写 roundtrip、
不存在的文件返回 404。
"""
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import prompts


@pytest.fixture()
def prompts_client(tmp_path, monkeypatch):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "base_prompt.txt").write_text("原始内容", encoding="utf-8")
    monkeypatch.setattr(prompts, "_PROMPTS_DIR", prompts_dir.resolve())

    app = FastAPI()
    app.include_router(prompts.router)
    return TestClient(app)


def test_list_prompts(prompts_client):
    response = prompts_client.get("/api/prompts")
    assert response.status_code == 200
    assert response.json() == ["base_prompt.txt"]


def test_get_prompt_roundtrip(prompts_client):
    fetched = prompts_client.get("/api/prompts/base_prompt.txt")
    assert fetched.status_code == 200
    assert fetched.json()["content"] == "原始内容"

    updated = prompts_client.put(
        "/api/prompts/base_prompt.txt", json={"content": "新内容"}
    )
    assert updated.status_code == 200
    assert (
        prompts_client.get("/api/prompts/base_prompt.txt").json()["content"] == "新内容"
    )


def test_get_missing_prompt_returns_404(prompts_client):
    response = prompts_client.get("/api/prompts/not_exist.txt")
    assert response.status_code == 404


def test_put_missing_prompt_returns_404(prompts_client):
    response = prompts_client.put(
        "/api/prompts/not_exist.txt", json={"content": "x"}
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "bad_filename",
    [
        "../secrets.env",
        "..%2Fsecrets.env",
        "../../xianyu_state.json",
        "a/b.txt",
        "..",
    ],
)
def test_path_traversal_rejected_on_get(prompts_client, bad_filename):
    response = prompts_client.get(f"/api/prompts/{bad_filename}")
    assert response.status_code in (400, 404, 422), (
        f"{bad_filename!r} 应被拒绝，实际返回 {response.status_code}"
    )


def test_get_dot_filename_is_normalized_to_list_by_http_client(prompts_client):
    """HTTP 客户端会把 /api/prompts/. 归一化为列表端点（非穿越向量），仅作行为记录。"""
    response = prompts_client.get("/api/prompts/.")
    assert response.status_code == 200


@pytest.mark.parametrize(
    "bad_filename",
    [
        "../secrets.env",
        "../../xianyu_state.json",
        "a/b.txt",
    ],
)
def test_path_traversal_rejected_on_put(prompts_client, bad_filename):
    response = prompts_client.put(
        f"/api/prompts/{bad_filename}", json={"content": "evil"}
    )
    assert response.status_code in (400, 404, 422)


def test_safe_prompt_path_containment(tmp_path, monkeypatch):
    """直接验证 containment 函数：绝对路径与反斜杠穿越都必须抛 HTTPException。"""
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    monkeypatch.setattr(prompts, "_PROMPTS_DIR", prompts_dir.resolve())

    from fastapi import HTTPException

    for bad in [
        str(tmp_path / "outside.txt"),  # 绝对路径
        "..\\..\\win.ini",  # Windows 反斜杠
        "sub/../../escape.txt",
    ]:
        with pytest.raises(HTTPException) as exc_info:
            prompts._safe_prompt_path(bad)
        assert exc_info.value.status_code == 400
