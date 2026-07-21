from __future__ import annotations

from copy import deepcopy


from aicrm_next.shared.typing import JsonDict


from .repo import _apply_customer_filters, _apply_page

class FixtureCustomerReadRepository:
    """Fixture read repository shaped for the future PostgreSQL projection repo."""

    def __init__(self) -> None:
        self._customers: list[JsonDict] = [
            {
                "unionid": "union_customer_001",
                "person_id": "person_001",
                "external_userid": "wx_ext_001",
                "customer_name": "张小蓝",
                "remark": "小蓝",
                "description": "9.9 试用后关注正式课安排",
                "owner_userid": "ZhaoYanFang",
                "owner_display_name": "赵艳芳",
                "mobile": "13800138000",
                "tags": ["付费意向", "黄小璨", "重点跟进"],
                "class_user_status": {
                    "current_status": "lead_trial",
                    "signup_status": "trial_9_9",
                    "signup_label_name": "9.9 试用",
                    "activation_bucket": "activated",
                    "updated_at": "2026-05-18T10:20:00+08:00",
                },
                "last_message_at": "2026-05-18T10:15:00+08:00",
                "last_touch_at": "2026-05-18T10:20:00+08:00",
                "updated_at": "2026-05-18T10:20:00+08:00",
                "binding": {"is_bound": True, "mobile": "13800138000", "binding_status": "bound"},
                "identity": {"unionid": "union_customer_001", "person_id": "person_001", "external_userid": "wx_ext_001", "mobile": "13800138000"},
                "follow_users": [{"userid": "ZhaoYanFang", "display_name": "赵艳芳", "is_primary": True}],
                "marketing_summary": {
                    "main_stage": "trial",
                    "sub_stage": "activated_focus",
                    "value_segment": "high_intent",
                    "last_dispatch_at": "2026-05-18T09:00:00+08:00",
                },
                "marketing_profile": {
                    "stage_key": "trial/activated_focus",
                    "recommended_action": "跟进正式课报名",
                    "signals": ["recent_reply", "high_intent_tag"],
                    "matched_questions": [
                        {
                            "questionnaire_id": "questionnaire_fixture_001",
                            "questionnaire_title": "黄小璨报名问卷",
                            "submission_id": "submission_fixture_001",
                            "submitted_at": "2026-05-18T09:30:00+08:00",
                            "question_id": "q_goal",
                            "question": "孩子当前英语学习目标是什么？",
                            "answer": "希望提升表达自信",
                        }
                    ],
                },
                "contact": {
                    "external_userid": "wx_ext_001",
                    "name": "张小蓝",
                    "remark": "小蓝",
                    "description": "9.9 试用后关注正式课安排",
                },
                "sidebar_context": {
                    "can_open_sidebar": True,
                    "marketing_stage": "activated_focus",
                    "customer_profile_url": "/admin/customers/union_customer_001",
                },
            },
            {
                "unionid": "union_customer_002",
                "person_id": "person_002",
                "external_userid": "wx_ext_002",
                "customer_name": "李未绑",
                "remark": "未绑手机号客户",
                "description": "已加微但尚未绑定手机号",
                "owner_userid": "LiuXiao",
                "owner_display_name": "刘晓",
                "mobile": None,
                "tags": ["新用户"],
                "class_user_status": {
                    "current_status": "new_user",
                    "signup_status": "",
                    "signup_label_name": "",
                    "activation_bucket": "pending_input",
                    "updated_at": "2026-05-17T18:05:00+08:00",
                },
                "last_message_at": "2026-05-17T18:00:00+08:00",
                "last_touch_at": "2026-05-17T18:05:00+08:00",
                "updated_at": "2026-05-17T18:05:00+08:00",
                "binding": {"is_bound": False, "mobile": None, "binding_status": "unbound"},
                "identity": {"unionid": "union_customer_002", "person_id": "person_002", "external_userid": "wx_ext_002", "mobile": None},
                "follow_users": [{"userid": "LiuXiao", "display_name": "刘晓", "is_primary": True}],
                "marketing_summary": {"main_stage": "new_user", "sub_stage": "pending_input", "value_segment": "unknown"},
                "marketing_profile": {
                    "stage_key": "new_user/pending_input",
                    "recommended_action": "补录手机号和激活状态",
                    "signals": ["missing_mobile"],
                },
                "contact": {
                    "external_userid": "wx_ext_002",
                    "name": "李未绑",
                    "remark": "未绑手机号客户",
                    "description": "已加微但尚未绑定手机号",
                },
                "sidebar_context": {
                    "can_open_sidebar": True,
                    "marketing_stage": "new_user",
                    "customer_profile_url": "/admin/customers/union_customer_002",
                },
            },
            {
                "unionid": "union_customer_003",
                "person_id": "person_003",
                "external_userid": "",
                "customer_name": "王缺失",
                "remark": "缺外部联系人",
                "description": "导入线索，尚未形成 external_userid",
                "owner_userid": "",
                "owner_display_name": "",
                "mobile": "13900139000",
                "tags": ["导入线索"],
                "class_user_status": {
                    "current_status": "lead_imported",
                    "signup_status": "",
                    "signup_label_name": "导入线索",
                    "activation_bucket": "not_activated",
                    "updated_at": "2026-05-16T12:00:00+08:00",
                },
                "last_message_at": None,
                "last_touch_at": None,
                "updated_at": "2026-05-16T12:00:00+08:00",
                "binding": {"is_bound": True, "mobile": "13900139000", "binding_status": "bound_no_external_userid"},
                "identity": {"unionid": "union_customer_003", "person_id": "person_003", "external_userid": "", "mobile": "13900139000"},
                "follow_users": [],
                "marketing_summary": {"main_stage": "lead", "sub_stage": "imported", "value_segment": "unknown"},
                "marketing_profile": {"stage_key": "lead/imported", "recommended_action": "等待加微", "signals": []},
                "contact": {"external_userid": "", "name": "王缺失", "remark": "缺外部联系人", "description": "导入线索"},
                "sidebar_context": {"can_open_sidebar": False, "marketing_stage": "lead_imported"},
            },
            {
                "unionid": "union_customer_004",
                "person_id": "person_004",
                "external_userid": "wx_ext_004",
                "customer_name": "陈复访",
                "remark": "复访客户",
                "description": "黄小璨未激活，需要再次触达",
                "owner_userid": "ZhaoYanFang",
                "owner_display_name": "赵艳芳",
                "mobile": "13700137000",
                "tags": ["黄小璨", "复访"],
                "class_user_status": {
                    "current_status": "followup",
                    "signup_status": "",
                    "signup_label_name": "复访",
                    "activation_bucket": "not_activated",
                    "updated_at": "2026-05-15T11:30:00+08:00",
                },
                "last_message_at": "2026-05-15T11:10:00+08:00",
                "last_touch_at": "2026-05-15T11:30:00+08:00",
                "updated_at": "2026-05-15T11:30:00+08:00",
                "binding": {"is_bound": True, "mobile": "13700137000", "binding_status": "bound"},
                "identity": {"unionid": "union_customer_004", "person_id": "person_004", "external_userid": "wx_ext_004", "mobile": "13700137000"},
                "follow_users": [{"userid": "ZhaoYanFang", "display_name": "赵艳芳", "is_primary": True}],
                "marketing_summary": {"main_stage": "followup", "sub_stage": "not_activated", "value_segment": "medium"},
                "marketing_profile": {
                    "stage_key": "followup/not_activated",
                    "recommended_action": "发送激活提醒",
                    "signals": ["not_activated"],
                },
                "contact": {
                    "external_userid": "wx_ext_004",
                    "name": "陈复访",
                    "remark": "复访客户",
                    "description": "黄小璨未激活，需要再次触达",
                },
                "sidebar_context": {
                    "can_open_sidebar": True,
                    "marketing_stage": "followup",
                    "customer_profile_url": "/admin/customers/union_customer_004",
                },
            },
            {
                "person_id": "person_masked_001",
                "external_userid": "external_user_masked_001",
                "customer_name": "customer_masked_001",
                "remark": "remark_masked_001",
                "description": "description_masked_001",
                "owner_userid": "owner_masked_001",
                "owner_display_name": "owner_masked_display_001",
                "mobile": "mobile_masked_001",
                "tags": [],
                "class_user_status": {
                    "current_status": "activated",
                    "signup_status": "activated",
                    "signup_label_name": "tag_masked_001",
                    "activation_bucket": "activated",
                    "updated_at": "2026-05-20T08:43:12+00:00",
                    "wecom_tag_sync_status": "skipped_fake_seed",
                    "wecom_tag_sync_error": "",
                },
                "last_message_at": "2026-05-20T08:43:12+00:00",
                "last_touch_at": "2026-05-20T08:43:12+00:00",
                "updated_at": "2026-05-20T08:43:12+00:00",
                "binding": {
                    "is_bound": True,
                    "person_id": 1,
                    "mobile": "mobile_masked_001",
                    "binding_status": "bound",
                    "third_party_user_id": "third_party_user_masked_001",
                },
                "identity": {
                    "person_id": 1,
                    "external_userid": "external_user_masked_001",
                    "mobile": "mobile_masked_001",
                    "unionid": "unionid_masked_001",
                    "openid": "openid_masked_001",
                    "status": "active",
                },
                "follow_users": [{"userid": "owner_masked_001", "display_name": "owner_masked_display_001", "is_primary": True}],
                "marketing_summary": {"main_stage": "activated", "sub_stage": "masked_sample", "value_segment": "fixture"},
                "marketing_profile": {
                    "stage_key": "activated/masked_sample",
                    "recommended_action": "masked sample only",
                    "signals": ["masked_sample"],
                },
                "contact": {
                    "external_userid": "external_user_masked_001",
                    "name": "customer_masked_001",
                    "remark": "remark_masked_001",
                    "description": "description_masked_001",
                },
                "sidebar_context": {
                    "can_open_sidebar": True,
                    "marketing_stage": "activated",
                    "customer_profile_url": "/admin/customers/external_user_masked_001",
                },
            },
        ]
        self._timeline: dict[str, list[JsonDict]] = {
            "wx_ext_001": [
                {
                    "event_id": "evt_001",
                    "event_type": "message",
                    "event_time": "2026-05-18T10:15:00+08:00",
                    "title": "客户发送新消息",
                    "summary": "想了解 9.9 试用后的正式课安排。",
                    "source_table": "archive_messages",
                    "source_id": "msg_001",
                    "metadata": {"msgtype": "text", "owner_userid": "ZhaoYanFang"},
                },
                {
                    "event_id": "evt_002",
                    "event_type": "tag",
                    "event_time": "2026-05-18T10:20:00+08:00",
                    "title": "标签更新",
                    "summary": "新增重点跟进标签。",
                    "source_table": "contact_tags",
                    "source_id": "tag_evt_001",
                    "metadata": {"tags": ["重点跟进"]},
                },
            ],
            "wx_ext_002": [
                {
                    "event_id": "evt_003",
                    "event_type": "contact_added",
                    "event_time": "2026-05-17T18:00:00+08:00",
                    "title": "客户已加微",
                    "summary": "客户进入新用户池，尚未绑定手机号。",
                    "source_table": "contacts",
                    "source_id": "wx_ext_002",
                    "metadata": {"binding_status": "unbound"},
                }
            ],
            "wx_ext_004": [
                {
                    "event_id": "evt_004",
                    "event_type": "message",
                    "event_time": "2026-05-15T11:10:00+08:00",
                    "title": "客户回复复访消息",
                    "summary": "客户询问激活入口是否还有效。",
                    "source_table": "archive_messages",
                    "source_id": "msg_004",
                    "metadata": {"msgtype": "text", "owner_userid": "ZhaoYanFang"},
                }
            ],
            "external_user_masked_001": [
                {
                    "event_id": "message:masked_001",
                    "event_type": "message",
                    "event_time": "2026-05-20T08:43:12+00:00",
                    "title": "消息 · text",
                    "summary": "masked message content 001",
                    "source_table": "archived_messages",
                    "source_id": "msg_masked_001",
                    "metadata": {"msgtype": "text", "owner_userid": "owner_masked_001"},
                },
                {
                    "event_id": "status_change:masked_001",
                    "event_type": "status_change",
                    "event_time": "2026-05-20T08:43:12+00:00",
                    "title": "状态变更",
                    "summary": "- -> activated",
                    "source_table": "class_user_status_history",
                    "source_id": "status_masked_001",
                    "metadata": {"signup_label_name": "tag_masked_001"},
                },
            ],
        }
        self._messages: dict[str, list[JsonDict]] = {
            "wx_ext_001": [
                {
                    "msgid": "msg_001",
                    "msgtype": "text",
                    "content": "我想了解正式课怎么报名",
                    "send_time": "2026-05-18T10:15:00+08:00",
                    "external_userid": "wx_ext_001",
                    "chat_type": "single",
                    "owner_userid": "ZhaoYanFang",
                    "sender": "customer",
                },
                {
                    "msgid": "msg_002",
                    "msgtype": "text",
                    "content": "老师什么时候方便介绍一下",
                    "send_time": "2026-05-18T10:10:00+08:00",
                    "external_userid": "wx_ext_001",
                    "chat_type": "single",
                    "owner_userid": "ZhaoYanFang",
                    "sender": "customer",
                }
            ],
            "wx_ext_002": [],
            "wx_ext_004": [
                {
                    "msgid": "msg_004",
                    "msgtype": "text",
                    "content": "激活入口还有效吗",
                    "send_time": "2026-05-15T11:10:00+08:00",
                    "external_userid": "wx_ext_004",
                    "chat_type": "single",
                    "owner_userid": "ZhaoYanFang",
                    "sender": "customer",
                }
            ],
            "external_user_masked_001": [
                {
                    "msgid": "msg_masked_001",
                    "msgtype": "text",
                    "content": "masked message content 001",
                    "send_time": "2026-05-20T08:43:12+00:00",
                    "external_userid": "external_user_masked_001",
                    "chat_type": "single",
                    "owner_userid": "owner_masked_001",
                    "sender": "customer",
                }
            ],
        }

    def list_customers(
        self,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]:
        rows = _apply_customer_filters([deepcopy(item) for item in self._customers], filters or {})
        return _apply_page(rows, limit=limit, offset=offset)

    def count_customers(self, filters: JsonDict | None = None) -> int:
        return len(_apply_customer_filters([deepcopy(item) for item in self._customers], filters or {}))

    def replace_all(
        self,
        *,
        customers: list[JsonDict],
        timeline_by_external_userid: dict[str, list[JsonDict]] | None = None,
        messages_by_external_userid: dict[str, list[JsonDict]] | None = None,
    ) -> None:
        self._customers = [deepcopy(item) for item in customers]
        self._timeline = {
            str(external_userid): [deepcopy(item) for item in items]
            for external_userid, items in (timeline_by_external_userid or {}).items()
        }
        self._messages = {
            str(external_userid): [deepcopy(item) for item in items]
            for external_userid, items in (messages_by_external_userid or {}).items()
        }

    seed = replace_all

    def get_customer(self, external_userid: str) -> JsonDict | None:
        item = next((item for item in self._customers if item.get("external_userid") == external_userid), None)
        return deepcopy(item) if item else None

    get_customer_detail = get_customer

    def get_customer_by_unionid(self, unionid: str) -> JsonDict | None:
        item = next((item for item in self._customers if str(item.get("unionid") or item.get("identity", {}).get("unionid") or "") == unionid), None)
        return deepcopy(item) if item else None

    def list_timeline(
        self,
        external_userid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]:
        rows = [deepcopy(item) for item in self._timeline.get(external_userid, [])]
        event_type = str((filters or {}).get("event_type") or "").strip()
        event_types = {str(value).strip() for value in (filters or {}).get("event_types") or [] if str(value).strip()}
        if event_type:
            rows = [item for item in rows if item.get("event_type") == event_type]
        elif event_types:
            rows = [item for item in rows if item.get("event_type") in event_types]
        rows.sort(key=lambda item: (str(item.get("event_time") or ""), str(item.get("event_id") or "")), reverse=True)
        return _apply_page(rows, limit=limit, offset=offset)

    get_customer_timeline = list_timeline

    def list_recent_messages(self, external_userid: str, *, limit: int | None = None) -> list[JsonDict]:
        return _apply_page([deepcopy(item) for item in self._messages.get(external_userid, [])], limit=limit, offset=0)

    get_recent_messages = list_recent_messages

    def list_timeline_by_unionid(
        self,
        unionid: str,
        filters: JsonDict | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[JsonDict]:
        customer = self.get_customer_by_unionid(unionid)
        external_userid = str((customer or {}).get("external_userid") or "")
        return self.list_timeline(external_userid, filters, limit=limit, offset=offset) if external_userid else []

    def list_recent_messages_by_unionid(self, unionid: str, *, limit: int | None = None) -> list[JsonDict]:
        customer = self.get_customer_by_unionid(unionid)
        external_userid = str((customer or {}).get("external_userid") or "")
        return self.list_recent_messages(external_userid, limit=limit) if external_userid else []

    def count_timeline_by_unionid(self, unionid: str, filters: JsonDict | None = None) -> int:
        return len(self.list_timeline_by_unionid(unionid, filters, limit=None, offset=0))

    def customer_exists(self, external_userid: str) -> bool:
        return self.get_customer(external_userid) is not None

    def customer_exists_by_unionid(self, unionid: str) -> bool:
        return self.get_customer_by_unionid(unionid) is not None
