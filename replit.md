# Travel Insurance Inquiry Processing Application

## Overview

This Flask-based application automates the processing of travel insurance inquiries by monitoring email inboxes and processing WhatsApp messages. It extracts customer information using OpenAI's API and provides a web dashboard for managing inquiries.

## System Architecture

The application follows a modern Flask architecture with background task processing:

### Backend Architecture
- **Flask Web Framework**: Main application framework with blueprint-based routing
- **SQLAlchemy ORM**: Database abstraction layer with PostgreSQL support
- **Task Queue System**: Custom PostgreSQL-based task queue for background processing
- **APScheduler**: Handles periodic email polling jobs

### Database Layer
- **Primary Database**: PostgreSQL (production) with SQLite fallback (development)
- **Models**: 
  - `User`: Authentication and user management
  - `Inquiry`: Central entity for customer inquiries
  - `Email`: Email message storage and metadata
  - `WhatsAppMessage`: WhatsApp message handling
  - `ExtractedData`: AI-extracted customer information
  - `PendingTask`: Background task queue management
- **Migrations**: Flask-Migrate/Alembic for schema management

### Frontend Architecture
- **Templates**: Jinja2 templating with Bootstrap styling
- **Static Assets**: CSS/JS with modern dark theme UI
- **DataTables**: Server-side processing for inquiry management

## Key Components

### Email Processing Pipeline
1. **Microsoft Graph Integration**: Polls email inbox using MS365 API
2. **Intent Classification**: OpenAI-powered email categorization
3. **Data Extraction**: Structured data extraction from email content
4. **Inquiry Matching**: Links multiple emails to single customer inquiries

### WhatsApp Integration
1. **Webhook Handler**: Receives incoming WhatsApp messages via Green API
2. **Message Processing**: Extracts travel information from WhatsApp content
3. **Media Support**: Handles text, images, and location messages

### Background Task System
- **Custom PostgreSQL Queue**: Replaces Redis-based RQ for better Replit compatibility
- **Worker Process**: Dedicated background worker (`postgres_worker.py`)
- **Task Types**: Email processing, WhatsApp message handling, periodic polling

### Dashboard Features
- **Inquiry Management**: View, edit, and update customer inquiries
- **Business Intelligence**: Revenue metrics, conversion tracking, high-value filtering
- **Export Capabilities**: CSV exports for business reporting
- **Status Tracking**: Visual indicators for inquiry completeness

## Data Flow

1. **Inbound Messages**: 
   - Emails arrive via Microsoft Graph polling
   - WhatsApp messages via webhook or polling
2. **Task Creation**: Messages queued as `PendingTask` records
3. **Background Processing**: Worker processes tasks asynchronously
4. **Data Extraction**: OpenAI API extracts structured information
5. **Inquiry Management**: Data consolidated into customer inquiries
6. **Dashboard Display**: Real-time updates in web interface

## External Dependencies

### Required APIs
- **Microsoft Graph API**: Email monitoring and retrieval
- **OpenAI API**: Text analysis and data extraction
- **Green API (WaAPI)**: WhatsApp Business API integration

### Infrastructure Services
- **PostgreSQL**: Primary database (Neon/Replit)
- **Redis**: Optional caching layer (removed in favor of PostgreSQL)

### Python Libraries
- Flask ecosystem (SQLAlchemy, Login, Migrate)
- Microsoft Authentication Library (MSAL)
- OpenAI Python client
- Background processing (APScheduler, custom worker)

## Deployment Strategy

### Environment Configuration
- **Environment Variables**: Managed via Replit Secrets
- **Configuration Classes**: Environment-specific settings in `config.py`
- **Database URLs**: Automatic PostgreSQL/SQLite detection

### Production Deployment
- **Simple Threading**: `run_production_simple.py` for single-process deployment
- **Multi-Process**: `run_production.py` for scaled deployment
- **Replit Optimization**: Configured for Replit's containerized environment

### Monitoring and Logging
- **Structured Logging**: Comprehensive application logging
- **Task Monitoring**: Background job status tracking
- **Error Handling**: Retry logic with exponential backoff

## Changelog
- June 12, 2025. Initial setup

## User Preferences

```
Preferred communication style: Simple, everyday language.
```