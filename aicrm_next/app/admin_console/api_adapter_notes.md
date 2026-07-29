# Legacy Frontend Adapter Notes

The frontend compatibility layer is a shell for copying the current AI-CRM admin templates and static assets without redesigning the UI.

Rules:

- Keep existing navigation, routes, filters, drawers, modals, table fields, labels, and visual density.
- Keep old templates and static files as the frontend baseline.
- Add adapter endpoints only when the new FastAPI response needs to preserve a legacy Flask JSON shape.
- Do not import the old backend packages from this experiment.
