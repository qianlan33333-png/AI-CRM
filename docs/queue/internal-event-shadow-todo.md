# Internal Event Shadow TODO

## Resolved: ai_campaign.created

`ai_campaign.created` is now implemented by
`CreateCloudCampaignCommand` in
`aicrm_next/extensions/growth/cloud_orchestrator/campaigns_write.py`.

The AI Campaign internal event vertical slice covers:

- `ai_campaign.created`
- `ai_campaign.approved`
- `ai_campaign.started`

See `docs/queue/internal-event-ai-campaign.md` for the current event schema,
consumers, feature flag, verification plan, and rollback instructions.
