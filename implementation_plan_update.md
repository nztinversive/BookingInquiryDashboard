# Implementation Plan: Travel Inquiry Processor Enhancements

This document outlines the phased plan to improve the existing email processing system and integrate WhatsApp messaging capabilities.

## Phase 1: Foundation Strengthening & Core Email Improvements

**Goal:** Enhance the stability, reliability, and maintainability of the existing email processing core.

**Tasks:**

1.  **Task Queue Implementation:**
    *   [X] Choose and integrate a task queue system (Celery with Redis).
    *   [X] Migrate background email polling and processing logic from `threading` to the task queue (`tasks.py`).
    *   [ ] Configure task workers and monitoring for the queue.
2.  **Enhanced Error Handling & Resilience:**
    *   [ ] Implement exponential backoff retries for MS Graph API calls.
    *   [ ] Implement exponential backoff retries for OpenAI API calls.
    *   [ ] Define and implement a 'dead-letter' or 'permanently_failed' status for emails that fail repeatedly.
    *   [ ] Add more granular error logging with context (email ID, task ID).
3.  **Dependency Management & Configuration:**
    *   [ ] Formalize dependencies using Poetry or PDM (`pyproject.toml`).
    *   [ ] Refactor configuration loading using Flask's standard practices (e.g., `config.py`).
4.  **Security Hardening (Initial):**
    *   [ ] Research and select a Secrets Management solution (Vault, Cloud Provider KMS, etc.).
    *   [ ] Migrate API keys and database credentials to the chosen Secrets Management tool.
    *   [ ] Review MS Graph API permissions for least privilege.

## Phase 2: WhatsApp Integration - Backend & Core Logic

**Goal:** Set up the infrastructure and backend logic to receive, store, and send WhatsApp messages.

**Tasks:**

1.  **WhatsApp Business API Setup:**
    *   [ ] Choose a WhatsApp Business API provider (Meta direct, Twilio, Vonage, etc.).
    *   [ ] Obtain necessary API credentials and set up the WhatsApp Business Account.
    *   [ ] Configure phone number(s) for the service.
2.  **Database Schema Updates:**
    *   [ ] Design database models for WhatsApp messages (`WhatsAppMessage`).
    *   [ ] Design relationship between `WhatsAppMessage` and `Inquiry` (or a potential new `Contact` model).
    *   [ ] Implement database migrations (e.g., using Flask-Migrate/Alembic).
3.  **Webhook Implementation:**
    *   [ ] Create a new Flask endpoint to receive incoming WhatsApp message webhooks.
    *   [ ] Implement security validation for incoming webhooks (e.g., signature verification).
    *   [ ] Parse incoming message data (text, media, sender info).
    *   [ ] Create service logic to find/create the relevant `Inquiry`/`Contact` and save the `WhatsAppMessage` to the database.
4.  **Outgoing Message Service:**
    *   [ ] Develop a service function to send outgoing WhatsApp messages via the chosen API provider.
    *   [ ] Implement basic templating or logic for sending acknowledgments or initial responses (optional).
5.  **Integration with Task Queue:**
    *   [ ] (Optional) Offload any heavy processing related to incoming WhatsApp messages (e.g., complex lookups, OpenAI calls) to the task queue established in Phase 1.

## Phase 3: Unified Dashboard & Frontend Development

**Goal:** Create a web interface for staff to view and manage both email and WhatsApp inquiries.

**Tasks:**

1.  **Core UI Structure:**
    *   [ ] Set up base Flask templates and static files.
    *   [ ] Implement user authentication/authorization for staff access.
2.  **Inquiry List View:**
    *   [ ] Create a dashboard page listing all inquiries.
    *   [ ] Display key information (sender/contact, last message timestamp, status, source - Email/WhatsApp).
    *   [ ] Implement searching and filtering capabilities (by date, status, source, contact info).
3.  **Inquiry Detail View:**
    *   [ ] Create a page to show the details of a single inquiry.
    *   [ ] Display extracted data (from emails or potentially WhatsApp).
    *   [ ] Display a unified conversation history including both emails and WhatsApp messages in chronological order.
    *   [ ] Show original email content and associated attachments.
    *   [ ] Display WhatsApp message content (text, media links).
4.  **Manual Actions (Optional):**
    *   [ ] Add functionality for staff to manually edit extracted data.
    *   [ ] Add functionality for staff to trigger outgoing WhatsApp messages (e.g., replies).
    *   [ ] Display processing errors clearly in the UI.

## Phase 4: Advanced Features, Optimization & Testing

**Goal:** Refine data processing, implement comprehensive monitoring, and ensure robustness through testing.

**Tasks:**

1.  **Refined Data Extraction & Validation:**
    *   [ ] Implement data validation for extracted data using Pydantic or similar.
    *   [ ] Refine OpenAI prompts based on initial results.
    *   [ ] Explore/implement NLP techniques (e.g., spaCy) for local data extraction from emails *and* WhatsApp messages if applicable.
    *   [ ] Implement input sanitization for data persisted to the database.
2.  **Optimized Email Fetching:**
    *   [ ] Evaluate and potentially implement MS Graph Delta Queries or Change Notifications (Webhooks) instead of timestamp polling.
3.  **Monitoring & Logging:**
    *   [ ] Implement structured logging (e.g., JSON format).
    *   [ ] Set up integration with a centralized logging platform (ELK, Splunk, Datadog, etc.).
    *   [ ] Implement application monitoring (key metrics: queue lengths, API latencies, error rates) using Prometheus/Grafana or similar.
4.  **Testing Suite:**
    *   [ ] Develop unit tests (`pytest`) for core logic (data extraction, API clients, database models, utility functions).
    *   [ ] Develop integration tests for component interactions (e.g., API webhook -> DB save).
    *   [ ] Implement end-to-end tests for key user flows in the web UI (if feasible).
5.  **Documentation:**
    *   [ ] Update `README.md` with final setup instructions, architecture, and environment variables.
    *   [ ] Add inline documentation/docstrings to the code. 