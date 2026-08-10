"""记忆的读 / 改 / 删（记忆与追问文档 §6「必须有的口子」）。

这三个接口**不是可选项**：

- 导出 —— 用户有权知道系统记了什么；
- 删除 —— 用户有权让系统忘掉；
- 单条纠正 —— 换了出发城市不该等三次采样才生效。

⚠️ `X-Profile-Id` 不是安全边界（文档 §5 方案 A）。它由客户端自己生成，
可猜——所以记忆里绝不能存敏感信息。鉴权仍由 `X-API-Key` 负责。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.api.deps import get_profile_id, require_api_key
from app.core.exceptions import AppError
from app.core.logging import get_logger
from app.models.errors import ErrorCode
from app.models.memory import (
    REMEMBERED_FIELDS,
    Preference,
    TripHistory,
)
from app.store import get_store

log = get_logger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"], dependencies=[Depends(require_api_key)])


class ProfileView(BaseModel):
    """导出：记住的全部内容。"""

    profile_id: str
    preferences: dict[str, Preference] = Field(default_factory=dict)
    history: list[TripHistory] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.preferences and not self.history


class PatchRequest(BaseModel):
    """单条纠正。`value=None` 表示忘掉这一条。"""

    key: str = Field(description=f"偏好字段名，取值范围：{', '.join(REMEMBERED_FIELDS)}")
    value: Any = Field(default=None)


def _require_profile(profile_id: str) -> str:
    if not profile_id:
        raise AppError(
            "缺少 X-Profile-Id 请求头，无法确定要操作哪一份记忆",
            code=ErrorCode.INVALID_PARAMS,
        )
    return profile_id


@router.get("/me", response_model=ProfileView)
async def get_profile(profile_id: str = Depends(get_profile_id)) -> ProfileView:
    """导出这份 profile 记住的一切。没有记忆时返回空结构而不是 404——
    "还没记住任何东西"是正常状态，不是错误。"""
    pid = _require_profile(profile_id)
    store = get_store()
    profile = await store.load_profile(pid)
    return ProfileView(
        profile_id=pid,
        preferences=profile.preferences if profile else {},
        history=await store.history(pid),
    )


@router.patch("/me", response_model=ProfileView)
async def patch_profile(
    payload: PatchRequest, profile_id: str = Depends(get_profile_id)
) -> ProfileView:
    """改掉或删掉某一条偏好。

    手工纠正直接置为**满置信度**：这是用户明说的，比采样三次更可信。
    """
    pid = _require_profile(profile_id)
    if payload.key not in REMEMBERED_FIELDS:
        raise AppError(
            f"不支持的偏好字段：{payload.key}（可用：{', '.join(REMEMBERED_FIELDS)}）",
            code=ErrorCode.INVALID_PARAMS,
        )

    store = get_store()
    if payload.value is None:
        await store.forget_preference(pid, payload.key)
    else:
        await store.patch_preference(pid, payload.key, payload.value)

    profile = await store.load_profile(pid)
    log.info("偏好已纠正", extra={"profile_id": pid, "key": payload.key})
    return ProfileView(
        profile_id=pid,
        preferences=profile.preferences if profile else {},
        history=await store.history(pid),
    )


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_profile(profile_id: str = Depends(get_profile_id)) -> None:
    """清空这份 profile 的全部记忆（偏好 + 履历）。不可撤销。"""
    pid = _require_profile(profile_id)
    await get_store().delete_profile(pid)
    log.info("记忆已清空", extra={"profile_id": pid})
