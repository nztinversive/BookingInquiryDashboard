# Travel Insurance Inquiry Processor

## Overview

This application automates the processing of incoming emails for a travel insurance agency. It monitors an email inbox, identifies potential customer inquiries, extracts relevant travel and contact information, and stores the structured data in a database for review and quoting by agency staff.

## Security Notice

⚠️ **Important**: This application requires several environment variables to be set for secure operation. **Never commit sensitive credentials to version control.** 

## Setup

### 1. Environment Variables

Copy the `env.example` file to `.env` and fill in your actual values:

```bash
cp env.example .env
```

Edit the `.env` file with your actual credentials. **Required variables:**

*   `SESSION_SECRET`: A strong secret key for Flask sessions (generate with `python -c "import secrets; print(secrets.token_hex(32))"`)
*   `DATABASE_URL`: Connection string for your database
*   `OPEN_API_KEY`: Your API key for OpenAI
*   `MS365_CLIENT_ID`, `MS365_CLIENT_SECRET`, `MS365_TENANT_ID`, `MS365_TARGET_EMAIL`: Microsoft Graph API credentials

**Optional variables for admin user creation:**
*   `ADMIN_USERNAME`, `ADMIN_PASSWORD`, `ADMIN_EMAIL`: Creates an initial admin user

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Database Setup

```bash
flask db upgrade  # If using migrations
# OR
python -c "from app import db; db.create_all()"  # For initial setup
```

### 4. Run the Application

```bash
python main.py
# OR
gunicorn --bind 0.0.0.0:5000 main:app
```

## How It Works

The application consists of a Flask web server and a background polling process:

1.  **Background Email Polling (`app/background_tasks.py`):**
    *   A background thread runs periodically (default: every 120 seconds).
    *   It connects to a Microsoft 365 mailbox using the Microsoft Graph API (`ms_graph_service.py`) to fetch new emails received since the last check.
    *   It uses a timestamp (`last_checked_timestamp`) to only retrieve new messages.

2.  **Email Filtering and Intent Classification:**
    *   Each new email summary (subject, sender, preview) is first checked against negative filters (specific senders/subject keywords like 'spam', 'undeliverable', 'solicitation').
    *   If not filtered out, the subject and body preview are sent to the OpenAI API (`data_extraction_service.py`) to classify the email's intent (e.g., 'inquiry', 'spam', 'other').
    *   Only emails classified as 'inquiry' proceed to full processing.

3.  **Data Extraction (`data_extraction_service.py`):**
    *   For 'inquiry' emails, the full email details (including HTML body) are fetched via the Graph API.
    *   The HTML body is parsed into plain text.
    *   **Local Extraction:** Basic regular expressions attempt to find common fields like dates, email, phone numbers, cost, destination, and origin (optimized for US states).
    *   **OpenAI Extraction:** The plain text content is sent to the OpenAI API (GPT-4o mini) with a specific prompt to extract a structured JSON object containing:
        *   `first_name`, `last_name`, `home_address`, `date_of_birth`
        *   `travel_start_date`, `travel_end_date`
        *   `trip_cost`, `trip_destination`, `origin`
        *   `initial_trip_deposit_date`
        *   `email`, `phone_number`
        *   `travelers` (array with `first_name`, `last_name`, `date_of_birth` for each)
    *   **Merging:** Results from local and OpenAI extraction are merged, preferring OpenAI's results for accuracy.
    *   **Fallback Logic:**
        *   If `initial_trip_deposit_date` is missing, it's calculated as 7 days before `travel_start_date` (if available).
    *   **Calculation:** `cost_per_traveler` is calculated by dividing `trip_cost` by the number of travelers.

4.  **Database Operations (`app/background_tasks.py`, `app/models.py`):**
    *   Uses Flask-SQLAlchemy to interact with a database.
    *   Finds or creates an `Inquiry` record based on the sender's email address.
    *   Creates an `Email` record storing details like subject, sender, received time, processing status ('processing', 'processed', 'error'), and the classified `intent`.
    *   Links the `Email` to the `Inquiry`.
    *   Finds the `ExtractedData` record associated with the `Inquiry`.
        *   If it exists, the newly extracted data is *merged* into the existing record (filling empty fields).
        *   If it doesn't exist, a new `ExtractedData` record is created.
    *   Creates `AttachmentMetadata` records for any email attachments.
    *   Updates the `Email` status to 'processed' or 'error'.
    *   Updates the `Inquiry` timestamp (`updated_at`).
    *   All database operations for a single email are wrapped in a transaction with rollback on error.

5.  **Web Interface (Flask - `app/__init__.py`, `app/routes.py` - *Assumed*):**
    *   Provides a web UI (details not specified in provided code) to likely display the processed inquiries and their extracted data from the database.

## Technology Stack

*   **Programming Language:** Python 3.x
*   **Web Framework:** Flask
*   **Database ORM:** SQLAlchemy (via Flask-SQLAlchemy)
*   **Database:** PostgreSQL / SQLite (configurable via `DATABASE_URL`)
*   **External APIs:**
    *   Microsoft Graph API (for email access)
    *   OpenAI API (GPT-4o mini for intent classification and data extraction)
*   **Key Libraries:**
    *   `requests` (assumed for API calls)
    *   `beautifulsoup4` (for HTML parsing)
    *   `python-dotenv` (assumed for environment variables)
    *   `threading` (for background polling)
