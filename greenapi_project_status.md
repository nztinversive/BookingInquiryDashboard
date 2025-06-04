# Green API Integration Project Status

## Phase 1 – Credentials & Config
- [x] **1. Load secrets in `config.py`**
  - `WAAPI_INSTANCE_ID`, `WAAPI_API_TOKEN`, `WAAPI_WEBHOOK_SECRET` confirmed in `config.py`.
  - Added check for `WAAPI_WEBHOOK_SECRET` in `ProductionConfig`.
- [x] **2. Unit-test env vars (`tests/test_config.py`)**
  - Created `tests/test_config.py`.
  - Added unit tests for `WAAPI_INSTANCE_ID`, `WAAPI_API_TOKEN`, and `WAAPI_WEBHOOK_SECRET`.

## Phase 2 – Inbound Plumbing
*(Chosen: Webhook Route)*
- [x] **3. A. Webhook route**
  - Created `app/whatsapp_routes.py` with a `/whatsapp` blueprint.
  - Implemented `POST /webhook` endpoint.
    - Verified `X-Waapi-HMAC` using `WAAPI_WEBHOOK_SECRET`.
    - Enqueues `process_whatsapp_message` task by creating a `PendingTask` record.
    - Returns `200 OK`.
- [ ] **4. B. Poller job** (Skipped)
- [x] **5. Model & migration**
  - `WhatsAppMessage` model and its relationship in `Inquiry` model were pre-existing in `app/models.py`.
  - Database migration `flask db upgrade` completed successfully after manual table drop.

## Phase 3 – Background Processing
- [x] **6. Extend `PendingTask.task_type`**
  - Acknowledged `'process_whatsapp_message'` as a new valid `task_type`. No direct model changes were needed.
- [x] **7. Handler (in `app/background_tasks.py`)**
  - Created `handle_new_whatsapp_message(payload, app_context)` in `app/background_tasks.py`.
    - Finds or creates an `Inquiry`.
    - Creates a `WhatsAppMessage` record.
    - Calls `extract_travel_data` (from `data_extraction_service.py`) on the message body/caption.
    - Upserts `ExtractedData`.
    - Updates `Inquiry` status to `'Complete'` or `'Incomplete'`.
    - Commits database session.
  - Updated `handle_task` dispatcher in `app/background_tasks.py` to include the new handler.
- [x] **8. Unit test handler (mock OpenAI)**
  - Created `tests/test_whatsapp_processing.py`.
  - Added `test_app` and `app_context` fixtures.
  - Added tests for:
    - New text message creating an inquiry.
    - Message linking to an existing inquiry.
    - Skipping duplicate message IDs.
    - Handling missing essential payload fields.
    - Processing media messages with captions.
    - Handling extraction failures (`Incomplete` status).
    - Handling messages with no text for extraction.
    - Handling media messages with no caption for existing inquiries.
    - Handling other message types (e.g., stickers) for new and existing inquiries.

## Phase 4 – UI Wiring
- [ ] **9. Dashboard badges**
- [ ] **10. Timeline template**
- [ ] **11. Status filter**

## Phase 5 – End-to-End QA
- [ ] **12. Webhook or polling smoke test**
- [ ] **13. Extraction QA**

## Phase 6 – Docs & Hand-off
- [ ] **14. Update `README.md`**
- [ ] **15. (Optional)** Generate ER diagram + Postman collection

---

### ✅ Definition of Done
Phone → Green API → webhook/poller → DB → OpenAI → dashboard — indistinguishable from email flow, minus spam filtering. 