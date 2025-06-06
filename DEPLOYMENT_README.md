# Production Deployment Guide for Replit

This guide explains how to deploy the email and WhatsApp processing application in production on Replit, ensuring both the web interface and background message processing work seamlessly.

## 🚀 Quick Start for Production

### 1. Environment Variables Setup

Before deploying, make sure these environment variables are set in your Replit Secrets:

**Required Variables:**
```
SESSION_SECRET=your-secure-random-string-here
DATABASE_URL=your-postgresql-connection-string
```

**For Email Processing:**
```
MS365_CLIENT_ID=your-microsoft-app-client-id
MS365_CLIENT_SECRET=your-microsoft-app-client-secret
MS365_TENANT_ID=your-microsoft-tenant-id
MS365_TARGET_EMAIL=mailbox-to-monitor@yourdomain.com
OPEN_API_KEY=your-openai-api-key
```

**For WhatsApp Processing (if using):**
```
WAAPI_API_TOKEN=your-whatsapp-api-token
WAAPI_INSTANCE_ID=your-whatsapp-instance-id
WAAPI_WEBHOOK_SECRET=your-webhook-secret
```

### 2. Deployment Options

You have two deployment options:

#### Option A: Simple Threading Approach (Recommended for Replit)
Run this command in the Replit console:
```bash
python run_production_simple.py
```

#### Option B: Multi-Process Approach (Alternative)
Run this command in the Replit console:
```bash
python run_production.py
```

**The simple approach is recommended** because it runs both the web server and background worker in the same process using threads, which is more reliable in Replit's environment.

### 3. Using Replit's Run Button

The application is already configured to work with Replit's Run button. Simply:

1. Click the **Run** button in Replit
2. The application will automatically start both:
   - The web server (accessible via the web preview)
   - The background worker (processing emails and WhatsApp messages)

## 🔧 How It Works

### Architecture Overview

The production setup includes:

1. **Web Server**: Serves the dashboard interface where you can view processed inquiries
2. **Background Worker**: Continuously processes new emails and WhatsApp messages
3. **Scheduler**: Creates periodic tasks to poll for new emails
4. **Database**: Stores all inquiries, messages, and extracted data

### Process Flow

1. **Email Processing**:
   - APScheduler creates polling tasks every 5 minutes (configurable)
   - Background worker fetches new emails from Microsoft 365
   - OpenAI extracts travel data from email content
   - Data is stored and linked to customer inquiries

2. **WhatsApp Processing**:
   - Webhook receives new WhatsApp messages
   - Background worker processes the message
   - Data extraction and storage follows the same flow

3. **Dashboard Updates**:
   - Web interface shows real-time data from the database
   - No manual switching between modes required

## 🎯 Production Features

### Automatic Process Management
- **Self-healing**: If either the web server or background worker crashes, it automatically restarts
- **Graceful shutdown**: Handles shutdown signals properly
- **Resource monitoring**: Logs process status and errors

### Logging
- Comprehensive logging with timestamps and thread/process identification
- Separate log levels for different components
- Error tracking with full stack traces

### Database Management
- Automatic table creation on first run
- Connection pooling for better performance
- Transaction management with rollback on errors

## 🛠️ Monitoring and Troubleshooting

### Checking Application Status

1. **Web Interface**: Access the Replit web preview to see the dashboard
2. **Logs**: Check the console output for real-time logging
3. **Database**: Use the manual email poll button in the dashboard to test connectivity

### Common Issues and Solutions

#### Web Interface Not Loading
```bash
# Check if the process is running
ps aux | grep python

# Restart if needed
python run_production_simple.py
```

#### Messages Not Being Processed
- Check environment variables are set correctly
- Verify API credentials (MS365, OpenAI, WhatsApp)
- Look for error messages in the console logs

#### Database Connection Issues
- Verify `DATABASE_URL` is set correctly
- Check if the PostgreSQL addon is properly configured
- Look for connection errors in the logs

### Manual Commands for Testing

```bash
# Test database connectivity
python -c "from app import create_app; app = create_app(); print('Database connection OK')"

# Create sample data for testing
flask seed-sample

# Check pending tasks
python -c "from app import create_app; from app.models import PendingTask; app = create_app(); app.app_context().push(); print(f'Pending tasks: {PendingTask.query.count()}')"
```

## 🔒 Security Considerations

### Environment Variables
- Never commit sensitive information to code
- Use Replit Secrets for all API keys and credentials
- Generate a strong, unique `SESSION_SECRET`

### Database Security
- Use SSL connections for database (included in most PostgreSQL connection strings)
- Regularly backup your database
- Monitor for unusual activity

## 📊 Performance Optimization

### Replit-Specific Settings
- The application is configured for Replit's resource limits
- Background tasks are throttled to avoid hitting rate limits
- Database connections are pooled and recycled

### Scaling Considerations
- For high-volume applications, consider upgrading to Replit's higher tiers
- Monitor memory usage and adjust polling intervals if needed
- Consider implementing message queuing for very high throughput

## 🎉 Success Indicators

When everything is working correctly, you should see:

1. ✅ Web dashboard loads without errors
2. ✅ New emails appear in the dashboard within 5-10 minutes
3. ✅ WhatsApp messages are processed immediately (if webhooks are configured)
4. ✅ Data extraction populates customer information automatically
5. ✅ Console logs show regular processing activity without errors

## 📞 Support

If you encounter issues:

1. Check the console logs for specific error messages
2. Verify all environment variables are set correctly
3. Test individual components using the manual commands above
4. Reach out with specific error messages and logs for faster troubleshooting

---

**Note**: This setup is specifically optimized for Replit's environment and handles the unique challenges of running both web servers and background workers in their platform. 