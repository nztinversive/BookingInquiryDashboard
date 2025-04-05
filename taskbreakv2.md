# Feature Task Breakdown (v2)

## 1. Manual Editing of Extracted Data

*   **Database (Optional but Recommended):**
    *   [ ] Consider adding an `updated_at` timestamp field to the `ExtractedData` model in `app/models.py`.
    *   [ ] Consider adding a `updated_by_user_id` field if tracking *who* made the edit is important (link to `User` model).
    *   [ ] Apply database migrations if using Alembic, or ensure `db.create_all()` handles the changes.
*   **Backend:**
    *   [ ] Define a new route (`GET /email/<string:graph_id>/edit`) in `app/routes.py` to render the edit form.
    *   [ ] Implement logic in this GET route to fetch the `Email` and its associated `ExtractedData`.
    *   [ ] Define a new route (`POST /extracted_data/<int:data_id>/update`) in `app/routes.py` to handle the form submission.
    *   [ ] Implement logic in the POST route to find the `ExtractedData` record by its `id`.
    *   [ ] Implement logic to receive updated data from the submitted form.
    *   [ ] Perform validation on the received data (ensure required fields exist, check formats if possible).
    *   [ ] Update the `data` JSON field of the `ExtractedData` record.
    *   [ ] Update metadata fields (e.g., `validation_status` to 'Manually Corrected', update `updated_at` timestamp).
    *   [ ] Add logging for the update operation (who updated what, when).
    *   [ ] Redirect back to the detail page (`email_detail`) upon successful update, possibly with a flash message.
    *   [ ] Handle potential errors (record not found, invalid data, database errors) and display appropriate feedback (e.g., flash messages on form).
*   **Frontend:**
    *   [ ] Create a new template file `app/templates/edit_extracted_data.html` (can extend `layout.html`).
    *   [ ] Design the form in `edit_extracted_data.html`:
        *   Display existing key-value pairs from `extracted_data.data` in editable input fields (e.g., `<input type="text">`).
        *   Ensure the form `action` points to the correct POST update route (`/extracted_data/<id>/update`).
        *   Include a "Save Changes" button and potentially a "Cancel" link.
    *   [ ] Add an "Edit" button on the `email_detail.html` page that links to the GET route for the edit form (e.g., `/email/<graph_id>/edit`).
    *   [ ] Ensure flash messages (for success/error) are displayed correctly on the detail page or edit form.

## 2. Handling Multiple Emails for the Same Inquiry

*   **Database Schema Changes (app/models.py):**
    *   [ ] Define a new `Inquiry` model (e.g., `Inquiry(id, primary_email_address, consolidated_data JSONB, status, created_at, updated_at)`). Add necessary fields.
    *   [ ] Modify the `Email` model: Add `inquiry_id = db.Column(db.Integer, db.ForeignKey('inquiry.id'), nullable=True, index=True)`. Make it nullable initially to handle existing emails.
    *   [ ] Modify the `ExtractedData` model: Add `inquiry_id = db.Column(db.Integer, db.ForeignKey('inquiry.id'), nullable=False, index=True)`. Change the `email_graph_id` foreign key to be nullable or remove if data is only linked to Inquiry. Ensure uniqueness constraint is appropriate (e.g., unique per inquiry).
    *   [ ] Apply database migrations or ensure `db.create_all()` works. Consider backfilling `inquiry_id` for existing emails if feasible.
*   **Backend Processing Logic (e.g., in background_tasks.py):**
    *   [ ] In the email processing function (`process_email`):
        *   [ ] Extract sender email address.
        *   [ ] **Inquiry Matching:** Attempt to find an existing `Inquiry` record based on the `primary_email_address`. Define matching criteria (exact match? recent timeframe?).
        *   [ ] **New Inquiry:** If no match found, create a new `Inquiry` record (set `primary_email_address`, initial `status`).
        *   [ ] **Existing Inquiry:** If match found, use the existing `Inquiry` record.
        *   [ ] Link the current `Email` record to the determined `Inquiry` by setting `email.inquiry_id`.
        *   [ ] **Data Extraction:** Perform data extraction on the current email as usual.
        *   [ ] **Data Merging:**
            *   Fetch the `ExtractedData` associated with the `Inquiry` (or create if it's the first email for the inquiry).
            *   Get the newly extracted key-value pairs.
            *   Define merging rules (e.g., fill empty fields in existing data, overwrite based on timestamp?, flag conflicts).
            *   Update the `data` field in the `ExtractedData` record associated with the `Inquiry`.
            *   Update the `Inquiry`'s `updated_at` timestamp and potentially its `status`.
        *   [ ] Save changes to `Email`, `Inquiry`, and `ExtractedData` records.
*   **Frontend/Routes:**
    *   [ ] Update dashboard route (`app/routes.py`) to query and display `Inquiry` records, possibly joining with `ExtractedData`.
    *   [ ] Update `dashboard.html` table columns and data source to reflect `Inquiry` data (e.g., primary email, consolidated status, date of last update).
    *   [ ] Decide on detail view: Modify `email_detail` to become `inquiry_detail` or create a new route/template.
    *   [ ] Update the detail route (`app/routes.py`) to fetch an `Inquiry` by ID, its associated `ExtractedData`, and potentially all linked `Email` records.
    *   [ ] Update the detail template (`app/templates/...detail.html`) to display consolidated data from `Inquiry`/`ExtractedData` and list associated emails.
    *   [ ] Update links on the dashboard to point to the correct inquiry detail view.

## 3. Better Filtering for Client Intent

*   **Strategy Selection:**
    *   [ ] Option A: Refined Keyword/Rule-Based Filtering.
    *   [ ] Option B: AI Intent Classification.
    *   Decision: Choose initial strategy (e.g., Start with Option A).
*   **Implementation (Option A - Rule-Based):**
    *   [ ] Define filtering criteria (specific keywords, negative keywords, sender domain rules) in a configuration file or environment variables.
    *   [ ] Modify email fetching logic (`ms_graph_service.py` or `background_tasks.py` where emails are first retrieved):
        *   [ ] Apply sender filtering rules.
        *   [ ] Apply subject/body keyword filtering rules.
    *   [ ] Only save/queue emails that pass the filters for further processing (data extraction).
    *   [ ] Add logging for emails filtered out by this step (reason, sender, subject).
*   **Implementation (Option B - AI Intent Classification):**
    *   [ ] Select AI service/model (OpenAI, Vertex AI, etc.).
    *   [ ] Store API keys securely (e.g., environment variables).
    *   [ ] Modify processing pipeline (`background_tasks.py`):
        *   [ ] Add an initial step after fetching an email, before extraction.
        *   [ ] Prepare relevant text (subject + body snippet) for the AI model.
        *   [ ] Call the AI classification API.
        *   [ ] Define expected intent labels (e.g., "quote_request", "spam", "question").
        *   [ ] Parse the AI response.
    *   [ ] Add an `intent` field to the `Email` model (`app/models.py`).
    *   [ ] Store the classified intent in the `Email.intent` field.
    *   [ ] **Decision Logic:** Based on the stored `intent`:
        *   Only run data extraction if `intent == 'quote_request'`.
        *   Decide actions for other intents (e.g., change `processing_status` to 'ignored_spam', 'needs_manual_reply').
    *   [ ] Add error handling for AI API calls (retries, logging).
    *   [ ] Update dashboard/detail views to potentially show the classified `intent`.
    *   [ ] Consider costs and rate limits of the AI service. 