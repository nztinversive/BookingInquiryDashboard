````markdown
# Green API ↔ Dashboard Integration Task List

## 🎯 Objective  
**Capture every incoming WhatsApp (Green API) message, extract data with OpenAI, and display it in the dashboard exactly like existing emails (no filtering).**

* **Repo:** [`nztinversive/BookingInquiryDashboard`](https://github.com/nztinversive/BookingInquiryDashboard)  
* **Stack:** Flask · SQLAlchemy · Alembic · APScheduler · pytest  
* **DB:** PostgreSQL (prod) / SQLite (tests) — Alembic migrations handle both  
* **Helper to reuse:** `app/utils/extraction.py::extract_travel_data`  

> **Choose a delivery mode in Phase&nbsp;2**: **Webhook** *or* **Polling**. You don’t need both unless desired.

---

## Phase 1 – Credentials & Config
1. **Load secrets in `config.py`**
   ```python
   WAAPI_INSTANCE_ID    = os.getenv("WAAPI_INSTANCE_ID")
   WAAPI_API_TOKEN      = os.getenv("WAAPI_API_TOKEN")
   WAAPI_WEBHOOK_SECRET = os.getenv("WAAPI_WEBHOOK_SECRET")
````

2. **Unit-test env vars** (`tests/test_config.py`) with `pytest` + `monkeypatch`.

---

## Phase 2 – Inbound Plumbing

*(pick **A** or **B**)*

3. **A. Webhook route**

   * `app/whatsapp_routes.py` → Blueprint `/whatsapp`
   * `POST /webhook`

     1. Verify `X-Waapi-HMAC` using `WAAPI_WEBHOOK_SECRET`
     2. Enqueue `process_whatsapp_message` task
     3. Return `200 OK`

4. **B. Poller job**

   * Add `poll_whatsapp_messages()` to `app/scheduler.py` (every 5 s)

     1. `GET /ReceiveNotification`
     2. Enqueue each payload
     3. `DELETE /DeleteNotification`

5. **Model & migration**

   * New `WhatsAppMessage` table
   * Add relationship in `Inquiry`:

     ```python
     whatsapp_messages = db.relationship(
         "WhatsAppMessage", backref="inquiry", lazy="dynamic"
     )
     ```

---

## Phase 3 – Background Processing

6. **Extend** `PendingTask.task_type` → add `'process_whatsapp_message'`.

7. **Handler** (`worker.py`)

   1. Fetch message
   2. `extract_travel_data(msg.body)`
   3. Upsert `ExtractedData`
   4. Set `inquiry.status` to `'Complete'` or `'Incomplete'`
   5. `db.session.commit()`

8. **Unit test** handler (mock OpenAI).

---

## Phase 4 – UI Wiring

9. **Dashboard badges** — show WhatsApp icon/label when `latest_source == "whatsapp"`.

10. **Timeline template** — render media link (paperclip) when `msg.media_url` present.

11. **Status filter** — add `'new_whatsapp'` to dropdown.

---

## Phase 5 – End-to-End QA

12. **Webhook or polling smoke test**

    * Use ngrok (for webhook) or rely on scheduler (for polling).
    * Send WhatsApp message → verify DB & dashboard entry.

13. **Extraction QA**

    * Send valid & junk messages → confirm `Complete` vs `Incomplete` statuses.

---

## Phase 6 – Docs & Hand-off

14. **Update `README.md`** — new env vars, webhook/poll setup, sample cURL.

15. **(Optional)** Generate ER diagram + Postman collection for API calls.

---

### ✅ Definition of Done

Phone → Green API → webhook/poller → DB → OpenAI → dashboard — indistinguishable from email flow, minus spam filtering.

```
```
