-- AI-CRM post-legacy baseline v1.
-- Apply only through scripts/ops/bootstrap_database.py to a truly empty PostgreSQL database.
-- This is the versioned schema contract that Alembic revision 0001 historically assumed.

CREATE TABLE IF NOT EXISTS conversion_dispatch_log (
            id BIGSERIAL PRIMARY KEY,
            external_userid TEXT NOT NULL DEFAULT '',
            dispatched_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS automation_member (
            id BIGSERIAL PRIMARY KEY,
            external_contact_id TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            owner_staff_id TEXT NOT NULL DEFAULT '',
            in_pool BOOLEAN NOT NULL DEFAULT FALSE,
            current_pool TEXT NOT NULL DEFAULT 'removed',
            follow_type TEXT NOT NULL DEFAULT '',
            current_audience_code TEXT NOT NULL DEFAULT 'pending_questionnaire',
            questionnaire_status TEXT NOT NULL DEFAULT '',
            joined_at TEXT NOT NULL DEFAULT '',
            last_ai_push_at TEXT NOT NULL DEFAULT '',
            ai_cooldown_until TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS automation_channel (
            id BIGSERIAL PRIMARY KEY,
            channel_type TEXT NOT NULL DEFAULT 'qrcode',
            carrier_type TEXT NOT NULL DEFAULT 'qrcode',
            channel_name TEXT NOT NULL DEFAULT '',
            channel_code TEXT NOT NULL DEFAULT '',
            scene_value TEXT NOT NULL DEFAULT '',
            qr_url TEXT NOT NULL DEFAULT '',
            qr_ticket TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            owner_staff_id TEXT NOT NULL DEFAULT '',
            customer_channel TEXT NOT NULL DEFAULT '',
            link_url TEXT NOT NULL DEFAULT '',
            final_url TEXT NOT NULL DEFAULT '',
            welcome_message TEXT NOT NULL DEFAULT '',
            welcome_image_library_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            welcome_miniprogram_library_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            welcome_attachment_library_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            auto_accept_friend BOOLEAN NOT NULL DEFAULT FALSE,
            entry_tag_id TEXT NOT NULL DEFAULT '',
            entry_tag_name TEXT NOT NULL DEFAULT '',
            entry_tag_group_name TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS automation_channel_contact (
            id BIGSERIAL PRIMARY KEY,
            channel_id BIGINT NOT NULL DEFAULT 0,
            external_contact_id TEXT NOT NULL DEFAULT '',
            external_userid TEXT NOT NULL DEFAULT '',
            owner_staff_id TEXT NOT NULL DEFAULT '',
            enter_count INTEGER NOT NULL DEFAULT 1,
            first_channel_entered_at TIMESTAMPTZ,
            last_channel_entered_at TIMESTAMPTZ,
            source_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name = 'automation_channel_contact'
                  AND column_name = 'external_contact_id'
            ) THEN
                CREATE UNIQUE INDEX IF NOT EXISTS uq_automation_channel_contact_external
                ON automation_channel_contact(channel_id, external_contact_id)
                WHERE external_contact_id <> '';
            END IF;
        END $$;

CREATE TABLE IF NOT EXISTS automation_ai_push_log (
            id BIGSERIAL PRIMARY KEY,
            member_id BIGINT NOT NULL DEFAULT 0,
            pushed_at TEXT NOT NULL DEFAULT ''
        );

CREATE TABLE IF NOT EXISTS automation_touch_delivery_log (
            id BIGSERIAL PRIMARY KEY,
            member_id BIGINT NOT NULL DEFAULT 0,
            trace_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            sent_at TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS outbound_tasks (
            id BIGSERIAL PRIMARY KEY,
            trace_id TEXT NOT NULL DEFAULT ''
        );

CREATE TABLE IF NOT EXISTS automation_agent_run (
            id BIGSERIAL PRIMARY KEY,
            trace_id TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS automation_agent_config (
            id BIGSERIAL PRIMARY KEY,
            agent_code TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            scenario_code TEXT NOT NULL DEFAULT '',
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS automation_workflow (
            id BIGSERIAL PRIMARY KEY,
            review_status TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS automation_sop_template (
            id BIGSERIAL PRIMARY KEY
        );

CREATE TABLE IF NOT EXISTS radar_links (
            id BIGSERIAL PRIMARY KEY,
            code TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            target_type TEXT NOT NULL DEFAULT 'link',
            media_item_id TEXT NOT NULL DEFAULT '',
            deleted_at TIMESTAMPTZ
        );

CREATE TABLE IF NOT EXISTS group_chats (
            chat_id TEXT PRIMARY KEY,
            group_name TEXT NOT NULL DEFAULT '',
            owner_userid TEXT NOT NULL DEFAULT '',
            notice TEXT NOT NULL DEFAULT '',
            member_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            create_time TEXT NOT NULL DEFAULT '',
            dismissed_at TEXT NOT NULL DEFAULT '',
            raw_payload TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS people (
            id BIGSERIAL PRIMARY KEY,
            mobile TEXT NOT NULL DEFAULT '',
            third_party_user_id TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS external_contact_bindings (
            external_userid TEXT PRIMARY KEY,
            person_id TEXT,
            first_owner_userid TEXT NOT NULL DEFAULT '',
            last_owner_userid TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS wecom_external_contact_identity_map (
            id BIGSERIAL PRIMARY KEY,
            external_userid TEXT NOT NULL DEFAULT '',
            unionid TEXT NOT NULL DEFAULT '',
            openid TEXT NOT NULL DEFAULT '',
            follow_user_userid TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS wecom_external_contact_follow_users (
            id BIGSERIAL PRIMARY KEY,
            external_userid TEXT NOT NULL DEFAULT '',
            user_id TEXT NOT NULL DEFAULT '',
            relation_status TEXT NOT NULL DEFAULT 'active',
            is_primary BOOLEAN NOT NULL DEFAULT FALSE,
            remark TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS crm_user_identity (
            unionid TEXT PRIMARY KEY,
            primary_external_userid TEXT NOT NULL DEFAULT '',
            external_userids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            primary_openid TEXT NOT NULL DEFAULT '',
            openids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            mobile TEXT NOT NULL DEFAULT '',
            mobile_normalized TEXT NOT NULL DEFAULT '',
            mobile_verified BOOLEAN NOT NULL DEFAULT FALSE,
            mobile_source TEXT NOT NULL DEFAULT '',
            customer_name TEXT NOT NULL DEFAULT '',
            remark TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            avatar TEXT NOT NULL DEFAULT '',
            gender INTEGER,
            profile_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            primary_owner_userid TEXT NOT NULL DEFAULT '',
            follow_users_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            legacy_person_id TEXT NOT NULL DEFAULT '',
            legacy_identity_map_ids_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            legacy_sources_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            identity_status TEXT NOT NULL DEFAULT 'active',
            unionid_resolved_at TIMESTAMPTZ,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_polled_at TIMESTAMPTZ,
            next_poll_at TIMESTAMPTZ,
            poll_attempt_count INTEGER NOT NULL DEFAULT 0,
            last_poll_error TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS crm_user_identity_resolution_queue (
            id BIGSERIAL PRIMARY KEY,
            source_type TEXT NOT NULL DEFAULT '',
            source_key TEXT NOT NULL DEFAULT '',
            source_table TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            corp_id TEXT NOT NULL DEFAULT '',
            external_userid TEXT NOT NULL DEFAULT '',
            openid TEXT NOT NULL DEFAULT '',
            mobile TEXT NOT NULL DEFAULT '',
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            raw_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            resolved_unionid TEXT NOT NULL DEFAULT '',
            conflict_reason TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT NOT NULL DEFAULT '',
            next_attempt_at TIMESTAMPTZ,
            resolved_at TIMESTAMPTZ,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE UNIQUE INDEX IF NOT EXISTS ux_crm_user_identity_resolution_queue_pending_source
        ON crm_user_identity_resolution_queue (source_type, source_key)
        WHERE status = 'pending' AND source_type <> '' AND source_key <> '';

CREATE TABLE IF NOT EXISTS questionnaires (
            id BIGSERIAL PRIMARY KEY,
            slug TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            lead_qr_title TEXT NOT NULL DEFAULT '',
            lead_qr_subtitle TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS questionnaire_submissions (
            id BIGSERIAL PRIMARY KEY,
            questionnaire_id BIGINT NOT NULL DEFAULT 0,
            respondent_key TEXT NOT NULL DEFAULT '',
            external_userid TEXT NOT NULL DEFAULT '',
            follow_user_userid TEXT NOT NULL DEFAULT '',
            mobile_snapshot TEXT NOT NULL DEFAULT '',
            source_channel TEXT NOT NULL DEFAULT '',
            campaign_id TEXT NOT NULL DEFAULT '',
            staff_id TEXT NOT NULL DEFAULT '',
            total_score INTEGER NOT NULL DEFAULT 0,
            final_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
            assessment_result_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
            result_token TEXT NOT NULL DEFAULT '',
            submitted_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS questionnaire_questions (
            id BIGSERIAL PRIMARY KEY,
            questionnaire_id BIGINT NOT NULL DEFAULT 0,
            type TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            required BOOLEAN NOT NULL DEFAULT FALSE,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            placeholder_text TEXT NOT NULL DEFAULT '',
            assessment_dimension_key TEXT NOT NULL DEFAULT '',
            sidebar_profile_field TEXT NOT NULL DEFAULT ''
        );

CREATE TABLE IF NOT EXISTS questionnaire_submission_answers (
            id BIGSERIAL PRIMARY KEY,
            submission_id BIGINT NOT NULL DEFAULT 0,
            question_id BIGINT NOT NULL DEFAULT 0,
            question_type TEXT NOT NULL DEFAULT '',
            question_title_snapshot TEXT NOT NULL DEFAULT '',
            selected_option_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            selected_option_texts_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
            selected_option_scores_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
            selected_option_tags_snapshot JSONB NOT NULL DEFAULT '[]'::jsonb,
            text_value TEXT NOT NULL DEFAULT '',
            score_contribution DOUBLE PRECISION NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS wechat_pay_products (
            id BIGSERIAL PRIMARY KEY,
            product_code TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL DEFAULT '',
            amount_total INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'CNY',
            status TEXT NOT NULL DEFAULT 'draft',
            enabled BOOLEAN NOT NULL DEFAULT FALSE,
            cta_text TEXT NOT NULL DEFAULT '立即报名',
            require_mobile BOOLEAN NOT NULL DEFAULT FALSE,
            lead_program_id BIGINT,
            lead_channel_id BIGINT,
            lead_qr_title TEXT NOT NULL DEFAULT '',
            lead_qr_subtitle TEXT NOT NULL DEFAULT '',
            completion_redirect_enabled BOOLEAN NOT NULL DEFAULT FALSE,
            completion_redirect_url TEXT NOT NULL DEFAULT '',
            completion_target_json JSONB,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

ALTER TABLE wechat_pay_products
        ADD COLUMN IF NOT EXISTS completion_redirect_enabled BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE wechat_pay_products
        ADD COLUMN IF NOT EXISTS completion_redirect_url TEXT NOT NULL DEFAULT '';

ALTER TABLE wechat_pay_products
        ADD COLUMN IF NOT EXISTS completion_target_json JSONB;

CREATE TABLE IF NOT EXISTS service_period_products (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            trade_product_id BIGINT NOT NULL REFERENCES wechat_pay_products(id) ON DELETE RESTRICT,
            link_slug TEXT NOT NULL,
            membership_config_id TEXT NOT NULL DEFAULT '',
            membership_config_name TEXT NOT NULL DEFAULT '',
            duration_days INTEGER NOT NULL CHECK (duration_days > 0),
            deleted BOOLEAN NOT NULL DEFAULT FALSE,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE UNIQUE INDEX IF NOT EXISTS uq_service_period_products_trade_product_id
        ON service_period_products (trade_product_id);

CREATE UNIQUE INDEX IF NOT EXISTS uq_service_period_products_link_slug
        ON service_period_products (link_slug);

CREATE INDEX IF NOT EXISTS idx_service_period_products_updated
        ON service_period_products (tenant_id, deleted, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS wechat_pay_orders (
            id BIGSERIAL PRIMARY KEY,
            out_trade_no TEXT NOT NULL DEFAULT '',
            transaction_id TEXT NOT NULL DEFAULT '',
            order_source TEXT NOT NULL DEFAULT '',
            client_order_ref TEXT NOT NULL DEFAULT '',
            product_code TEXT NOT NULL DEFAULT '',
            product_name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            amount_total INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'CNY',
            unionid TEXT NOT NULL DEFAULT '',
            payer_name_snapshot TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'created',
            trade_state TEXT NOT NULL DEFAULT '',
            prepay_id TEXT NOT NULL DEFAULT '',
            bank_type TEXT NOT NULL DEFAULT '',
            payer_total INTEGER NOT NULL DEFAULT 0,
            success_url TEXT NOT NULL DEFAULT '',
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            request_meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            request_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            response_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            notify_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            last_error TEXT NOT NULL DEFAULT '',
            expires_at TIMESTAMPTZ,
            paid_at TIMESTAMPTZ,
            refunded_amount_total INTEGER NOT NULL DEFAULT 0,
            refund_status TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS wechat_pay_refunds (
            id BIGSERIAL PRIMARY KEY,
            order_id BIGINT NOT NULL DEFAULT 0,
            out_trade_no TEXT NOT NULL DEFAULT '',
            transaction_id TEXT NOT NULL DEFAULT '',
            out_refund_no TEXT NOT NULL DEFAULT '',
            refund_id TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            refund_amount_total INTEGER NOT NULL DEFAULT 0,
            order_amount_total INTEGER NOT NULL DEFAULT 0,
            currency TEXT NOT NULL DEFAULT 'CNY',
            status TEXT NOT NULL DEFAULT '',
            requested_by TEXT NOT NULL DEFAULT '',
            request_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            response_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            error_message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE TABLE IF NOT EXISTS service_period_entitlements (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            service_product_id BIGINT NOT NULL REFERENCES service_period_products(id) ON DELETE RESTRICT,
            trade_product_id BIGINT NOT NULL REFERENCES wechat_pay_products(id) ON DELETE RESTRICT,
            unionid TEXT NOT NULL,
            external_userid_snapshot TEXT NOT NULL DEFAULT '',
            membership_config_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','expired','disabled','refunded')),
            start_at TIMESTAMPTZ NOT NULL,
            end_at TIMESTAMPTZ NOT NULL,
            last_order_id BIGINT,
            last_out_trade_no TEXT NOT NULL DEFAULT '',
            renewal_count INTEGER NOT NULL DEFAULT 0,
            metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (tenant_id, service_product_id, unionid)
        );

CREATE INDEX IF NOT EXISTS idx_service_period_entitlements_product_status_end
        ON service_period_entitlements (service_product_id, status, end_at);

CREATE INDEX IF NOT EXISTS idx_service_period_entitlements_unionid
        ON service_period_entitlements (unionid);

CREATE INDEX IF NOT EXISTS idx_service_period_entitlements_last_order
        ON service_period_entitlements (last_order_id);

CREATE TABLE IF NOT EXISTS service_period_events (
            id BIGSERIAL PRIMARY KEY,
            tenant_id TEXT NOT NULL DEFAULT 'aicrm',
            event_id TEXT NOT NULL UNIQUE,
            service_product_id BIGINT NOT NULL REFERENCES service_period_products(id) ON DELETE RESTRICT,
            entitlement_id BIGINT REFERENCES service_period_entitlements(id) ON DELETE SET NULL,
            trade_product_id BIGINT NOT NULL REFERENCES wechat_pay_products(id) ON DELETE RESTRICT,
            order_id BIGINT,
            out_trade_no TEXT NOT NULL DEFAULT '',
            unionid TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL CHECK (event_type IN ('activated','renewed','expired','disabled','refunded','grant_failed_missing_unionid','membership_sync_failed','admin_adjusted')),
            duration_days INTEGER NOT NULL DEFAULT 0,
            before_start_at TIMESTAMPTZ,
            before_end_at TIMESTAMPTZ,
            after_start_at TIMESTAMPTZ,
            after_end_at TIMESTAMPTZ,
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE UNIQUE INDEX IF NOT EXISTS uq_service_period_events_event_once
        ON service_period_events (tenant_id, event_type, out_trade_no)
        WHERE out_trade_no <> '';

CREATE INDEX IF NOT EXISTS idx_service_period_events_product_created
        ON service_period_events (service_product_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_service_period_events_unionid_created
        ON service_period_events (unionid, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS wechat_pay_order_events (
            id BIGSERIAL PRIMARY KEY,
            out_trade_no TEXT NOT NULL DEFAULT '',
            event_type TEXT NOT NULL DEFAULT '',
            transaction_id TEXT NOT NULL DEFAULT '',
            trade_state TEXT NOT NULL DEFAULT '',
            payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            headers_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

CREATE INDEX IF NOT EXISTS ix_wechat_pay_order_events_trade_created_id
        ON wechat_pay_order_events (out_trade_no, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS automation_channel_qrcode_asset (
    id BIGSERIAL PRIMARY KEY,
    corp_id TEXT NOT NULL DEFAULT '',
    channel_id BIGINT NOT NULL REFERENCES automation_channel(id) ON DELETE CASCADE,
    scene_value TEXT NOT NULL DEFAULT '',
    config_id TEXT NOT NULL DEFAULT '',
    qr_url TEXT NOT NULL DEFAULT '',
    qr_url_hash TEXT NOT NULL DEFAULT '',
    provider_name TEXT NOT NULL DEFAULT '',
    provider_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'active',
    generation_source TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    generated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_callback_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (corp_id, scene_value)
);

CREATE INDEX IF NOT EXISTS idx_automation_channel_qrcode_asset_channel_status
ON automation_channel_qrcode_asset (channel_id, status, generated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS automation_channel_scene_alias (
    id BIGSERIAL PRIMARY KEY,
    corp_id TEXT NOT NULL DEFAULT '',
    channel_id BIGINT NOT NULL REFERENCES automation_channel(id) ON DELETE CASCADE,
    scene_value TEXT NOT NULL DEFAULT '',
    config_id TEXT NOT NULL DEFAULT '',
    qr_url TEXT NOT NULL DEFAULT '',
    carrier_type TEXT NOT NULL DEFAULT 'qrcode',
    provider_name TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    source TEXT NOT NULL DEFAULT '',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (corp_id, scene_value)
);

CREATE INDEX IF NOT EXISTS idx_automation_channel_scene_alias_channel_status
ON automation_channel_scene_alias (channel_id, status, updated_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS contacts (
    id BIGSERIAL PRIMARY KEY,
    unionid TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_contacts_unionid_updated
ON contacts (unionid, updated_at DESC, id DESC)
WHERE unionid <> '';

CREATE TABLE IF NOT EXISTS sync_runs (
    id BIGSERIAL PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'running',
    start_time TIMESTAMPTZ,
    end_time TIMESTAMPTZ,
    owner_userid TEXT NOT NULL DEFAULT '',
    cursor TEXT NOT NULL DEFAULT '',
    fetched_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    raw_response JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_status_created
ON sync_runs (status, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS wecom_external_contact_event_logs (
    id BIGSERIAL PRIMARY KEY,
    corp_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    change_type TEXT NOT NULL DEFAULT '',
    external_userid TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    event_time TEXT NOT NULL DEFAULT '',
    event_key TEXT NOT NULL DEFAULT '',
    payload_xml TEXT NOT NULL DEFAULT '',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    process_status TEXT NOT NULL DEFAULT 'pending',
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_wecom_external_contact_event_logs_event_key
ON wecom_external_contact_event_logs (event_key)
WHERE event_key <> '';

CREATE INDEX IF NOT EXISTS idx_wecom_external_contact_event_logs_status_created
ON wecom_external_contact_event_logs (process_status, created_at DESC, id DESC);

ALTER TABLE automation_agent_config
ADD COLUMN IF NOT EXISTS published_role_prompt TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS published_task_prompt TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS published_variables_json JSONB NOT NULL DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS published_output_schema_json JSONB NOT NULL DEFAULT '[]'::jsonb,
ADD COLUMN IF NOT EXISTS published_version INTEGER NOT NULL DEFAULT 0;

ALTER TABLE automation_agent_run
ADD COLUMN IF NOT EXISTS run_id TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS request_id TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS batch_id TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS userid TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS unionid TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS agent_code TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS agent_type TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS provider TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS input_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS variables_snapshot_json JSONB NOT NULL DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS final_prompt_preview TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS role_prompt_version INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS task_prompt_version INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS error_code TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS error_message TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS latency_ms INTEGER NOT NULL DEFAULT 0,
ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS parent_run_id TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS replay_of_run_id TEXT NOT NULL DEFAULT '',
ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

CREATE TABLE IF NOT EXISTS automation_agent_output (
    id BIGSERIAL PRIMARY KEY,
    output_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    userid TEXT NOT NULL DEFAULT '',
    unionid TEXT NOT NULL DEFAULT '',
    agent_code TEXT NOT NULL DEFAULT '',
    output_type TEXT NOT NULL DEFAULT '',
    raw_output_text TEXT NOT NULL DEFAULT '',
    normalized_output_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    rendered_output_text TEXT NOT NULL DEFAULT '',
    target_agent_code TEXT NOT NULL DEFAULT '',
    target_pool TEXT NOT NULL DEFAULT '',
    confidence NUMERIC NOT NULL DEFAULT 0,
    reason TEXT NOT NULL DEFAULT '',
    need_human_review BOOLEAN NOT NULL DEFAULT FALSE,
    applied_status TEXT NOT NULL DEFAULT '',
    adopted_by TEXT NOT NULL DEFAULT '',
    adopted_action TEXT NOT NULL DEFAULT '',
    outcome_status TEXT NOT NULL DEFAULT '',
    outcome_value TEXT NOT NULL DEFAULT '',
    revision_of_output_id TEXT NOT NULL DEFAULT '',
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_automation_agent_output_run_created
ON automation_agent_output (run_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS automation_agent_llm_call_log (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL DEFAULT '',
    agent_code TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT '',
    model TEXT NOT NULL DEFAULT '',
    model_name TEXT NOT NULL DEFAULT '',
    request_id TEXT NOT NULL DEFAULT '',
    prompt_hash TEXT NOT NULL DEFAULT '',
    request_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT '',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_automation_agent_llm_call_log_agent_created
ON automation_agent_llm_call_log (agent_code, created_at DESC, id DESC);
