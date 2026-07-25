from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aicrm_next.shared.runtime_settings import managed_runtime_setting, runtime_setting


Json = dict[str, Any]


class WeComTagLiveGateway:
    def __init__(self, *, client: Any | None = None, api_base: str | None = None, timeout: int | None = None) -> None:
        self._client = client
        self._api_base = (api_base or os.getenv("AICRM_WECOM_TAG_API_BASE", "https://qyapi.weixin.qq.com")).rstrip("/")
        self._timeout = timeout or int(os.getenv("AICRM_WECOM_TAG_TIMEOUT_SECONDS", "15") or "15")

    def _build_client(self) -> Any:
        if self._client is not None:
            return self._client
        return self

    def _access_token(self) -> str:
        corp_id = (
            os.getenv("AICRM_WECOM_TAG_CORP_ID")
            or managed_runtime_setting("WECOM_CORP_ID")
            or ""
        )
        secret = (
            runtime_setting("AICRM_WECOM_TAG_AGENT_SECRET")
            or runtime_setting("WECOM_CONTACT_SECRET")
            or runtime_setting("WECOM_SECRET")
        )
        if not corp_id.strip() or not secret.strip():
            raise RuntimeError("WECOM_CORP_ID and WECOM_CONTACT_SECRET are required for WeCom tag sync")
        query = urlencode(
            {
                "corpid": corp_id,
                "corpsecret": secret,
            }
        )
        with urlopen(f"{self._api_base}/cgi-bin/gettoken?{query}", timeout=self._timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("errcode") != 0:
            raise RuntimeError(f"wecom token request failed: errcode={payload.get('errcode')}")
        return str(payload.get("access_token") or "")

    def _post(self, path: str, payload: Json) -> Json:
        token = self._access_token()
        query = urlencode({"access_token": token})
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self._api_base}{path}?{query}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=self._timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
        if result.get("errcode") not in (0, None):
            raise RuntimeError(f"wecom tag request failed: errcode={result.get('errcode')}")
        return dict(result or {})

    def list_wecom_tags_live(self) -> Json:
        client = self._build_client()
        if client is self:
            return self._post("/cgi-bin/externalcontact/get_corp_tag_list", {})
        return dict(client.list_wecom_tags_live() if hasattr(client, "list_wecom_tags_live") else client.list_tags({}) or {})

    def add_corp_tag_live(
        self,
        *,
        group_id: str = "",
        group_name: str = "",
        tags: list[Json],
        group_order: int | None = None,
        agentid: int | None = None,
    ) -> Json:
        client = self._build_client()
        payload: Json = {"tag": list(tags or [])}
        if str(group_id or "").strip():
            payload["group_id"] = str(group_id or "").strip()
        if str(group_name or "").strip():
            payload["group_name"] = str(group_name or "").strip()
        if group_order is not None:
            payload["order"] = int(group_order)
        if agentid is not None:
            payload["agentid"] = int(agentid)
        if client is self:
            return self._post("/cgi-bin/externalcontact/add_corp_tag", payload)
        if hasattr(client, "add_corp_tag_live"):
            return dict(client.add_corp_tag_live(group_id=group_id, group_name=group_name, tags=tags, group_order=group_order, agentid=agentid) or {})
        if hasattr(client, "add_corp_tag"):
            return dict(client.add_corp_tag(payload) or {})
        raise RuntimeError("client does not support add_corp_tag")

    def edit_corp_tag_live(self, *, tag_or_group_id: str, name: str, order: int | None = None) -> Json:
        client = self._build_client()
        payload: Json = {"id": str(tag_or_group_id or "").strip(), "name": str(name or "").strip()}
        if order is not None:
            payload["order"] = int(order)
        if client is self:
            return self._post("/cgi-bin/externalcontact/edit_corp_tag", payload)
        if hasattr(client, "edit_corp_tag_live"):
            return dict(client.edit_corp_tag_live(tag_or_group_id=tag_or_group_id, name=name, order=order) or {})
        if hasattr(client, "edit_corp_tag"):
            return dict(client.edit_corp_tag(payload) or {})
        raise RuntimeError("client does not support edit_corp_tag")

    def delete_corp_tag_live(self, *, tag_ids: list[str] | None = None, group_ids: list[str] | None = None) -> Json:
        client = self._build_client()
        payload: Json = {
            "tag_id": [str(item or "").strip() for item in list(tag_ids or []) if str(item or "").strip()],
            "group_id": [str(item or "").strip() for item in list(group_ids or []) if str(item or "").strip()],
        }
        if client is self:
            return self._post("/cgi-bin/externalcontact/del_corp_tag", payload)
        if hasattr(client, "delete_corp_tag_live"):
            return dict(client.delete_corp_tag_live(tag_ids=tag_ids, group_ids=group_ids) or {})
        if hasattr(client, "del_corp_tag"):
            return dict(client.del_corp_tag(payload) or {})
        raise RuntimeError("client does not support del_corp_tag")

    def mark_tags_live(self, *, external_userid: str, tag_ids: list[str], operator: str) -> Json:
        client = self._build_client()
        payload = {"userid": operator, "external_userid": external_userid, "add_tag": list(tag_ids)}
        if client is self:
            return self._post("/cgi-bin/externalcontact/mark_tag", payload)
        return dict(client.mark_tags_live(external_userid=external_userid, tag_ids=tag_ids, operator=operator) if hasattr(client, "mark_tags_live") else client.mark_tag(payload) or {})

    def unmark_tags_live(self, *, external_userid: str, tag_ids: list[str], operator: str) -> Json:
        client = self._build_client()
        payload = {"userid": operator, "external_userid": external_userid, "remove_tag": list(tag_ids)}
        if client is self:
            return self._post("/cgi-bin/externalcontact/mark_tag", payload)
        return dict(client.unmark_tags_live(external_userid=external_userid, tag_ids=tag_ids, operator=operator) if hasattr(client, "unmark_tags_live") else client.mark_tag(payload) or {})


def build_wecom_tag_live_gateway() -> WeComTagLiveGateway:
    return WeComTagLiveGateway()
