"""
账号管理路由集成测试

覆盖：名称白名单校验（阻断路径穿越）、CRUD、非法 JSON 拒绝、
重复创建 409、删除 404。状态目录通过 env_manager 注入临时目录。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routes import accounts
from src.infrastructure.config.env_manager import env_manager

VALID_COOKIE = '{"cookies": {"unb": "12345"}}'


@pytest.fixture()
def accounts_client(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        env_manager,
        "get_value",
        lambda key, default=None: str(state_dir) if key == "ACCOUNT_STATE_DIR" else default,
    )

    app = FastAPI()
    app.include_router(accounts.router)
    return TestClient(app)


def test_create_and_get_account(accounts_client):
    response = accounts_client.post(
        "/api/accounts", json={"name": "acc1", "content": VALID_COOKIE}
    )
    assert response.status_code == 200
    assert response.json()["name"] == "acc1"

    fetched = accounts_client.get("/api/accounts/acc1")
    assert fetched.status_code == 200
    assert fetched.json()["content"] == VALID_COOKIE


def test_list_accounts(accounts_client):
    accounts_client.post("/api/accounts", json={"name": "acc1", "content": VALID_COOKIE})
    accounts_client.post("/api/accounts", json={"name": "acc2", "content": VALID_COOKIE})
    names = {item["name"] for item in accounts_client.get("/api/accounts").json()}
    assert names == {"acc1", "acc2"}


def test_update_account(accounts_client):
    accounts_client.post("/api/accounts", json={"name": "acc1", "content": VALID_COOKIE})
    updated = accounts_client.put(
        "/api/accounts/acc1", json={"content": '{"cookies": {"unb": "999"}}'}
    )
    assert updated.status_code == 200
    assert (
        accounts_client.get("/api/accounts/acc1").json()["content"]
        == '{"cookies": {"unb": "999"}}'
    )


def test_delete_account(accounts_client):
    accounts_client.post("/api/accounts", json={"name": "acc1", "content": VALID_COOKIE})
    assert accounts_client.delete("/api/accounts/acc1").status_code == 200
    assert accounts_client.get("/api/accounts/acc1").status_code == 404
    assert accounts_client.delete("/api/accounts/acc1").status_code == 404


def test_create_duplicate_returns_409(accounts_client):
    accounts_client.post("/api/accounts", json={"name": "acc1", "content": VALID_COOKIE})
    response = accounts_client.post(
        "/api/accounts", json={"name": "acc1", "content": VALID_COOKIE}
    )
    assert response.status_code == 409


@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil",
        "..%2Fevil",
        "../../xianyu_state",
        "a/b",
        "a b",
        "名称",
        "",
        "x" * 51,
        ".",
        "..",
    ],
)
def test_validate_name_rejects_bad_names(bad_name):
    """名称白名单：穿越片段、空串、超长、非法字符一律 400。"""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        accounts._validate_name(bad_name)
    assert exc_info.value.status_code == 400


@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil",
        "..%2Fevil",
        "../../xianyu_state",
        "a/b",
        "a b",
        "名称",
        "x" * 51,
        "..",
    ],
)
def test_path_traversal_names_rejected(accounts_client, bad_name):
    for method, payload in (
        ("get", None),
        ("post", {"name": bad_name, "content": VALID_COOKIE}),
        ("delete", None),
    ):
        if method == "get":
            response = accounts_client.get(f"/api/accounts/{bad_name}")
        elif method == "post":
            response = accounts_client.post("/api/accounts", json=payload)
        else:
            response = accounts_client.delete(f"/api/accounts/{bad_name}")
        assert response.status_code in (400, 404, 422), (
            f"{method} {bad_name!r} 应被拒绝，实际返回 {response.status_code}"
        )


def test_get_dot_name_is_normalized_to_list_by_http_client(accounts_client):
    """HTTP 客户端会把 /api/accounts/. 归一化为列表端点（非穿越向量），仅作行为记录。"""
    response = accounts_client.get("/api/accounts/.")
    assert response.status_code == 200


def test_invalid_json_content_rejected(accounts_client):
    response = accounts_client.post(
        "/api/accounts", json={"name": "acc1", "content": "not-json{{"}
    )
    assert response.status_code == 400
