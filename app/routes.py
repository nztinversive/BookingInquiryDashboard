from flask import Blueprint, render_template, current_app, Response, abort, url_for, request, redirect, flash # Added request, redirect, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import SQLAlchemyError # Import specific exception
from sqlalchemy.orm import joinedload, selectinload # Added selectinload
from sqlalchemy import or_ # Import or_ for search
from .models import Email, ExtractedData, User, Inquiry, WhatsAppMessage, PendingTask # Added PendingTask model
from . import db # Import the db object
import json # Import json for potential type casting
from operator import attrgetter # Import attrgetter for sorting
from datetime import datetime, timezone, timedelta # Import timezone for naive datetime comparison

# Removed direct import of poll_new_emails from background_tasks
# from app.background_tasks import poll_new_emails

def format_display_name(email_address):
    """Format email addresses for display names, especially WhatsApp ones"""
    if not email_address:
        return "Unknown Customer"
    
    # Check if it's a WhatsApp email
    if '@internal.placeholder' in email_address and 'whatsapp_' in email_address:
        try:
            # Extract phone number between 'whatsapp_' and '@c.us'
            phone_part = email_address.replace('whatsapp_', '').split('@')[0]
            if len(phone_part) >= 10:  # Valid phone number length
                # Format as (XXX) XXX-XXXX for US numbers or +XX XXX XXX XXXX for international
                if len(phone_part) == 11 and phone_part.startswith('1'):
                    # US number with country code
                    formatted = f"({phone_part[1:4]}) {phone_part[4:7]}-{phone_part[7:]}"
                    return f"WhatsApp: {formatted}"
                elif len(phone_part) == 10:
                    # US number without country code
                    formatted = f"({phone_part[:3]}) {phone_part[3:6]}-{phone_part[6:]}"
                    return f"WhatsApp: {formatted}"
                else:
                    # International number
                    return f"WhatsApp: +{phone_part}"
            else:
                return "WhatsApp Contact"
        except Exception:
            return "WhatsApp Contact"
    
    # Regular email - truncate if too long
    if len(email_address) > 25:
        return f"{email_address[:22]}..."
    else:
        return email_address

# Create a Blueprint for the main application routes
# Remove explicit static_folder here to rely on app-level static handling
main_bp = Blueprint('main', __name__,
                    template_folder='templates')

@main_bp.route('/dashboard/all_inquiries')
@login_required
def all_inquiries_dashboard():
    """Render the main dashboard page, showing unified inquiries with filtering."""
    try:
        # Get filter parameters from request arguments
        status_filter = request.args.get('status', default=None, type=str)
        search_query = request.args.get('search', default=None, type=str)

        # Base query
        query = Inquiry.query.options(joinedload(Inquiry.extracted_data))

        # Apply status filter
        if status_filter:
            query = query.filter(Inquiry.status == status_filter)

        # Apply search filter (across multiple fields)
        if search_query:
            search_term = f"%{search_query}%"
            query = query.join(Inquiry.emails, isouter=True) # Outer join to include inquiries with no emails
            query = query.join(Inquiry.whatsapp_messages, isouter=True) # Outer join for WhatsApp messages
            
            query = query.filter(
                or_(
                    Inquiry.primary_email_address.ilike(search_term), 
                    Email.subject.ilike(search_term), 
                    Email.sender_address.ilike(search_term),
                    WhatsAppMessage.body.ilike(search_term),
                    WhatsAppMessage.sender_number.ilike(search_term),
                    # Add search on extracted data fields if needed (requires JSONB functions or careful casting)
                    # ExtractedData.data['first_name'].astext.ilike(search_term) # Example (PostgreSQL specific)
                )
            ).distinct() # Use distinct because joins might create duplicate inquiries

        # Apply final ordering and execute query
        inquiries = query.order_by(Inquiry.updated_at.desc(), Inquiry.created_at.desc()).all()

    except Exception as e:
        current_app.logger.error(f"Error fetching inquiries for dashboard: {e}", exc_info=True)
        inquiries = []
        flash("Error loading dashboard data.", "danger")

    # Calculate counts (consider including new_whatsapp status)
    total_count = len(inquiries)
    complete_count = sum(1 for i in inquiries if i.status == 'Complete' or i.status == 'Manually Corrected')
    incomplete_count = sum(1 for i in inquiries if i.status == 'Incomplete' or i.status == 'new' or i.status == 'new_whatsapp') # Add new_whatsapp
    error_count = sum(1 for i in inquiries if i.status == 'Error' or i.status == 'Processing Failed')

    # Prepare data for the template, finding the latest communication for each inquiry
    dashboard_data = []
    for inquiry in inquiries:
        latest_comm = None
        latest_comm_type = None
        latest_timestamp = None

        try:
            # Get latest email
            latest_email = inquiry.emails.order_by(Email.received_at.desc()).first()
            if latest_email and latest_email.received_at:
                latest_comm = latest_email
                latest_comm_type = 'email'
                latest_timestamp = latest_email.received_at
            
            # Get latest WhatsApp message
            latest_wa_message = inquiry.whatsapp_messages.order_by(WhatsAppMessage.received_at.desc()).first()
            
            # Compare with latest email (if any)
            if latest_wa_message and latest_wa_message.received_at: 
                if latest_timestamp is None or latest_wa_message.received_at > latest_timestamp:
                    latest_comm = latest_wa_message
                    latest_comm_type = 'whatsapp'
                    latest_timestamp = latest_wa_message.received_at # Use received_at for consistent comparison
                    
        except Exception as comm_query_err:
            current_app.logger.error(f"Error fetching latest communication for Inquiry {inquiry.id}: {comm_query_err}")
        
        dashboard_data.append({
            'inquiry': inquiry,
            'latest_communication': latest_comm,
            'communication_type': latest_comm_type
        })

    # Pass filter values back to the template if needed for other JS logic
    return render_template('dashboard.html', 
                           user=current_user, 
                           dashboard_items=dashboard_data, # Renamed variable for clarity
                           total_count=total_count,
                           complete_count=complete_count,
                           incomplete_count=incomplete_count,
                           error_count=error_count,
                           # Pass current filters back to potentially pre-fill or use in JS
                           # request.args already accessible in template, so not strictly needed here
                           # status_filter=status_filter,
                           # search_query=search_query 
                           )

@main_bp.route('/')
@main_bp.route('/dashboard')
@login_required
def dashboard_customer_view():
    """Render the customer-centric dashboard view as the default."""
    try:
        # Base query: Fetch inquiries and their extracted data
        inquiries_with_data = Inquiry.query.options(
            joinedload(Inquiry.extracted_data)
        ).order_by(Inquiry.updated_at.desc(), Inquiry.created_at.desc()).all()

        customer_view_items = []
        for inquiry in inquiries_with_data:
            data = inquiry.extracted_data
            display_name = "Unknown Customer"
            num_travelers = 0

            if data:
                first_name = data.data.get('first_name') or ''
                last_name = data.data.get('last_name') or ''
                if first_name.strip() or last_name.strip():
                    display_name = f"{first_name} {last_name}".strip()
                elif inquiry.primary_email_address:
                    display_name = format_display_name(inquiry.primary_email_address)
                
                if isinstance(data.data.get('travelers'), list):
                    num_travelers = len(data.data.get('travelers'))
            elif inquiry.primary_email_address: # Fallback if no extracted_data but inquiry exists
                display_name = format_display_name(inquiry.primary_email_address)


            customer_view_items.append({
                'inquiry': inquiry,
                'data': data, # Pass the whole ExtractedData object (or its .data dict)
                'display_name': display_name,
                'num_travelers': num_travelers
            })

    except Exception as e:
        current_app.logger.error(f"Error fetching data for customer view dashboard: {e}", exc_info=True)
        customer_view_items = []
        flash("Error loading customer view data.", "danger")

    return render_template('dashboard_customer_view.html',
                           user=current_user,
                           customer_items=customer_view_items)

@main_bp.route('/manual_email_poll', methods=['POST']) # Use POST to avoid accidental triggering via GET
@login_required
def manual_email_poll_route():
    """Manually trigger the email polling process by creating a PendingTask."""
    try:
        current_app.logger.info(f"Manual email poll initiated by user: {current_user.username}. Creating PendingTask.")
        
        # Create a new PendingTask to trigger the poll_new_emails function via the worker
        new_poll_task = PendingTask(
            task_type='poll_all_new_emails', # This type is handled by the dispatcher in background_tasks
            status='pending',
            payload={}, # No specific payload needed for this trigger task
            scheduled_for=datetime.now(timezone.utc) # Schedule for immediate processing
        )
        db.session.add(new_poll_task)
        db.session.commit()
        
        flash("Manual email poll task has been successfully queued. Emails will be checked shortly.", "success")
    except SQLAlchemyError as e:
        db.session.rollback()
        current_app.logger.error(f"Database error during manual email poll task creation: {e}", exc_info=True)
        flash(f"Database error queuing manual email poll. Please try again or check logs.", "danger")
    except Exception as e:
        current_app.logger.error(f"Error during manual email poll task creation: {e}", exc_info=True)
        flash(f"An error occurred while trying to queue the email poll: {e}", "danger")
    return redirect(url_for('.dashboard_customer_view'))

# Add other main routes for your dashboard here
# Example:
# @main_bp.route('/profile')
# @login_required
# def profile():
#    return render_template('profile.html', user=current_user)

@main_bp.route('/export_csv')
@login_required
def export_csv():
    """Placeholder route for CSV export."""
    # TODO: Implement actual CSV export logic here
    # For now, return a simple response or redirect
    # return Response("CSV Export Not Implemented Yet", mimetype='text/plain')
    # Or redirect back to dashboard with a flash message:
    # from flask import flash, redirect
    # flash("CSV Export feature is not yet implemented.", "warning")
    # return redirect(url_for('.dashboard'))
    # Or just abort with a 404 for now:
    abort(404, description="CSV Export Not Implemented Yet")

# === NEW CLIENT-READY EXPORT ROUTES ===

@main_bp.route('/export/high-value')
@login_required
def export_high_value_inquiries():
    """Export high-value inquiries (>$5000) to CSV format."""
    try:
        import csv
        from io import StringIO
        
        # Query high-value inquiries
        inquiries = Inquiry.query.options(joinedload(Inquiry.extracted_data)).all()
        
        # Filter for high-value trips
        high_value_inquiries = []
        for inquiry in inquiries:
            if inquiry.extracted_data and inquiry.extracted_data.data:
                trip_cost = inquiry.extracted_data.data.get('trip_cost', '0')
                try:
                    cost_value = float(trip_cost) if trip_cost and trip_cost != 'N/A' else 0
                    if cost_value > 5000:
                        high_value_inquiries.append(inquiry)
                except (ValueError, TypeError):
                    continue
        
        # Create CSV output
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'Inquiry ID', 'Customer Name', 'Email', 'Trip Cost', 'Destination',
            'Travel Start Date', 'Travel End Date', 'Travelers', 'Status', 'Created Date'
        ])
        
        # Write data rows
        for inquiry in high_value_inquiries:
            data = inquiry.extracted_data.data if inquiry.extracted_data else {}
            
            first_name = data.get('first_name') or ''
            last_name = data.get('last_name') or ''
            customer_name = f"{first_name} {last_name}".strip()
            if not customer_name:
                customer_name = inquiry.primary_email_address
            
            trip_cost = data.get('trip_cost', 'N/A')
            destination = data.get('trip_destination', 'N/A')
            start_date = data.get('travel_start_date', 'N/A')
            end_date = data.get('travel_end_date', 'N/A')
            travelers = len(data.get('travelers', [])) if isinstance(data.get('travelers'), list) else 'N/A'
            
            writer.writerow([
                inquiry.id,
                customer_name,
                inquiry.primary_email_address,
                trip_cost,
                destination,
                start_date,
                end_date,
                travelers,
                inquiry.status,
                inquiry.created_at.strftime('%Y-%m-%d') if inquiry.created_at else 'N/A'
            ])
        
        output.seek(0)
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=high_value_inquiries.csv'}
        )
        
    except Exception as e:
        current_app.logger.error(f"Error exporting high-value inquiries: {e}", exc_info=True)
        flash("Error generating export. Please try again.", "danger")
        return redirect(url_for('.dashboard_customer_view'))

@main_bp.route('/export/ready-to-quote')
@login_required
def export_ready_to_quote():
    """Export complete inquiries ready for quoting to CSV format."""
    try:
        import csv
        from io import StringIO
        
        # Query completed inquiries
        inquiries = Inquiry.query.options(joinedload(Inquiry.extracted_data)).filter(
            Inquiry.status.in_(['Complete', 'Manually Corrected'])
        ).all()
        
        # Create CSV output
        output = StringIO()
        writer = csv.writer(output)
        
        # Write header
        writer.writerow([
            'Inquiry ID', 'Customer Name', 'Email', 'Phone', 'Trip Cost', 'Destination',
            'Travel Start Date', 'Travel End Date', 'Travelers', 'Home Address', 
            'Status', 'Data Completeness', 'Created Date'
        ])
        
        # Write data rows
        for inquiry in inquiries:
            data = inquiry.extracted_data.data if inquiry.extracted_data else {}
            
            first_name = data.get('first_name') or ''
            last_name = data.get('last_name') or ''
            customer_name = f"{first_name} {last_name}".strip()
            if not customer_name:
                customer_name = inquiry.primary_email_address
            
            # Calculate data completeness
            filled_count = 0
            total_required = 5
            
            # Check each required field properly
            if data.get('first_name') and data.get('first_name') != 'N/A' and len(str(data.get('first_name'))) > 0:
                filled_count += 1
            if data.get('last_name') and data.get('last_name') != 'N/A' and len(str(data.get('last_name'))) > 0:
                filled_count += 1
            if data.get('travel_start_date') and data.get('travel_start_date') != 'N/A' and len(str(data.get('travel_start_date'))) > 0:
                filled_count += 1
            if data.get('travel_end_date') and data.get('travel_end_date') != 'N/A' and len(str(data.get('travel_end_date'))) > 0:
                filled_count += 1
            if (data.get('trip_destination') and data.get('trip_destination') != 'N/A' and 
                len(str(data.get('trip_destination'))) > 0 and 
                str(data.get('trip_destination')).lower() not in ['none', 'not specified']):
                filled_count += 1
                
            completeness = f"{(filled_count / total_required * 100):.0f}%"
            
            trip_cost = data.get('trip_cost', 'N/A')
            destination = data.get('trip_destination', 'N/A')
            start_date = data.get('travel_start_date', 'N/A')
            end_date = data.get('travel_end_date', 'N/A')
            travelers = len(data.get('travelers', [])) if isinstance(data.get('travelers'), list) else 'N/A'
            phone = data.get('phone_number', 'N/A')
            home_address = data.get('home_address', 'N/A')
            
            writer.writerow([
                inquiry.id,
                customer_name,
                inquiry.primary_email_address,
                phone,
                trip_cost,
                destination,
                start_date,
                end_date,
                travelers,
                home_address,
                inquiry.status,
                completeness,
                inquiry.created_at.strftime('%Y-%m-%d') if inquiry.created_at else 'N/A'
            ])
        
        output.seek(0)
        
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': 'attachment; filename=ready_to_quote_inquiries.csv'}
        )
        
    except Exception as e:
        current_app.logger.error(f"Error exporting ready-to-quote inquiries: {e}", exc_info=True)
        flash("Error generating export. Please try again.", "danger")
        return redirect(url_for('.dashboard_customer_view'))

@main_bp.route('/reports/business-summary')
@login_required
def business_summary_report():
    """Generate a comprehensive business summary report."""
    try:
        # Fetch all inquiries with extracted data
        inquiries = Inquiry.query.options(joinedload(Inquiry.extracted_data)).all()
        
        # Calculate business metrics
        total_inquiries = len(inquiries)
        total_pipeline_value = 0
        high_value_count = 0
        complete_count = 0
        pending_count = 0
        error_count = 0
        destinations = {}
        
        for inquiry in inquiries:
            # Status counts
            if inquiry.status in ['Complete', 'Manually Corrected']:
                complete_count += 1
            elif inquiry.status in ['new', 'Incomplete', 'new_whatsapp']:
                pending_count += 1
            elif inquiry.status in ['Error', 'Processing Failed']:
                error_count += 1
            
            # Pipeline value and destinations
            if inquiry.extracted_data and inquiry.extracted_data.data:
                data = inquiry.extracted_data.data
                trip_cost = data.get('trip_cost', '0')
                try:
                    cost_value = float(trip_cost) if trip_cost and trip_cost != 'N/A' else 0
                    total_pipeline_value += cost_value
                    if cost_value > 5000:
                        high_value_count += 1
                except (ValueError, TypeError):
                    pass
                
                # Track destinations
                destination = data.get('trip_destination', 'Unknown')
                if destination and destination != 'N/A':
                    destinations[destination] = destinations.get(destination, 0) + 1
        
        # Calculate averages and rates
        avg_trip_value = total_pipeline_value / total_inquiries if total_inquiries > 0 else 0
        completion_rate = (complete_count / total_inquiries * 100) if total_inquiries > 0 else 0
        
        # Top destinations
        top_destinations = sorted(destinations.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Render the report template
        return render_template('business_summary_report.html',
                               user=current_user,
                               total_inquiries=total_inquiries,
                               total_pipeline_value=total_pipeline_value,
                               avg_trip_value=avg_trip_value,
                               high_value_count=high_value_count,
                               complete_count=complete_count,
                               pending_count=pending_count,
                               error_count=error_count,
                               completion_rate=completion_rate,
                               top_destinations=top_destinations,
                               report_date=datetime.now().strftime('%B %d, %Y'))
        
    except Exception as e:
        current_app.logger.error(f"Error generating business summary report: {e}", exc_info=True)
        flash("Error generating business report. Please try again.", "danger")
        return redirect(url_for('.dashboard_customer_view'))

# --- New Route for Email Details ---
@main_bp.route('/email/<string:graph_id>')
@login_required
def email_detail(graph_id):
    """Show details for a specific email."""
    try:
        # Query for the email and eagerly load its extracted data
        # Use first_or_404 to automatically return 404 if not found
        email = Email.query.options(
            db.joinedload(Email.extracted_data),
            db.joinedload(Email.attachments) # Also load attachments if needed later
        ).filter_by(graph_id=graph_id).first_or_404()

    except Exception as e:
        # Log if fetching the latest email fails for a specific inquiry
        current_app.logger.error(f"Error fetching email details for graph_id {graph_id}: {e}")
        abort(500, description="Error retrieving email details.") # Internal server error

    return render_template('email_detail.html', email=email, user=current_user)
# --- End New Route --- 

# --- Routes for Editing Extracted Data ---

# Changed route to accept data_id instead of graph_id
@main_bp.route('/extracted_data/<int:data_id>/edit', methods=['GET'])
@login_required
def edit_extracted_data_form(data_id):
    """Display the form to edit extracted data for a specific Inquiry."""
    # Fetch the ExtractedData object directly
    extracted_data = ExtractedData.query.options(
        joinedload(ExtractedData.inquiry) # Load the inquiry for context
    ).get_or_404(data_id)

    if not extracted_data:
        # This case is less likely with get_or_404, but good practice
        flash(f"No extracted data found for ID {data_id} to edit.", "warning")
        # Redirect back to dashboard if inquiry context is lost
        return redirect(url_for('.dashboard_customer_view')) 

    # Pass the ExtractedData object and inquiry context to the template
    return render_template('edit_extracted_data.html', 
                           extracted_data=extracted_data, 
                           # Use inquiry's primary address for context instead of email subject
                           inquiry_context=extracted_data.inquiry.primary_email_address if extracted_data.inquiry else "Unknown Inquiry",
                           user=current_user)

@main_bp.route('/extracted_data/<int:data_id>/update', methods=['POST'])
@login_required
def update_extracted_data(data_id):
    """Handle the submission of the edited extracted data form."""
    # Find the specific ExtractedData record
    data_to_update = ExtractedData.query.options(
        joinedload(ExtractedData.inquiry) # Load inquiry to get its ID for redirect
    ).get_or_404(data_id)
    # Get the inquiry_id before potential changes
    inquiry_id_for_redirect = data_to_update.inquiry_id 

    try:
        # 1. Get data from request.form and reconstruct the 'data' dictionary
        updated_data = {}
        # Iterate through existing keys to reconstruct the structure
        # This prevents arbitrary new fields from being added via form injection
        if data_to_update.data:
            for key in data_to_update.data.keys():
                form_field_name = f'data_{key}'
                if form_field_name in request.form:
                    # Assign the submitted value. Add type casting if needed.
                    # For simplicity, treating all as strings now.
                    updated_data[key] = request.form[form_field_name]
                else:
                    # If a field wasn't submitted (e.g., complex type), keep original
                    updated_data[key] = data_to_update.data[key]
        
        # 2. Validate (Basic example: check if essential fields are empty - adapt as needed)
        # essential_fields = ['first_name', 'last_name', 'email'] # Example
        # if any(not updated_data.get(field) for field in essential_fields):
        #     flash("Essential fields cannot be empty.", "danger")
        #     # Re-render the edit form with current (unsaved) data
        #     return render_template('edit_extracted_data.html', 
        #                            extracted_data=data_to_update, # Pass the original object
        #                            current_form_data=request.form, # Pass form data for repopulation
        #                            email_subject=data_to_update.email.subject, 
        #                            user=current_user)

        # 3. Update fields
        data_to_update.data = updated_data # Update the JSON field
        data_to_update.validation_status = 'Manually Corrected'
        data_to_update.updated_by_user_id = current_user.id # Assumes current_user is the User object
        # updated_at is handled automatically by onupdate=func.now()

        # 4. Commit the changes
        db.session.commit()

        # 5. Add logging
        current_app.logger.info(f"User {current_user.username} (ID: {current_user.id}) updated ExtractedData ID: {data_id}")

        # 6. Redirect back to inquiry detail page with flash message
        flash("Extracted data updated successfully.", "success")
        # Redirect to inquiry detail using the stored inquiry_id
        if inquiry_id_for_redirect:
             return redirect(url_for('.inquiry_detail', inquiry_id=inquiry_id_for_redirect))
        else:
             # Fallback to dashboard if inquiry_id wasn't found (shouldn't happen with get_or_404)
             flash("Could not determine inquiry to redirect back to.", "warning")
             return redirect(url_for('.dashboard_customer_view'))

    except SQLAlchemyError as e:
        db.session.rollback() # Rollback in case of DB error
        current_app.logger.error(f"Database error updating ExtractedData ID {data_id}: {e}")
        flash("Database error occurred while saving changes.", "danger")
    except Exception as e:
        db.session.rollback() # Rollback for any other unexpected errors
        current_app.logger.error(f"Unexpected error updating ExtractedData ID {data_id}: {e}")
        flash("An unexpected error occurred.", "danger")

    # If any error occurred, redirect back to the inquiry detail page
    # Redirecting to detail page to avoid losing context if form re-render is complex
    if inquiry_id_for_redirect:
        return redirect(url_for('.inquiry_detail', inquiry_id=inquiry_id_for_redirect))
    else:
        # Fallback if redirect inquiry ID isn't available
        return redirect(url_for('.dashboard_customer_view')) 

# --- End Edit Routes --- 

# --- New Route for Inquiry Details ---
@main_bp.route('/inquiry/<int:inquiry_id>')
@login_required
def inquiry_detail(inquiry_id):
    """Show details for a specific Inquiry, its data, and unified conversation history."""
    try:
        # Query for the inquiry, eagerly load extracted data
        inquiry = Inquiry.query.options(
            joinedload(Inquiry.extracted_data)
        ).get_or_404(inquiry_id)

        # Fetch associated communications
        emails = inquiry.emails.order_by(Email.received_at.asc()).all()
        whatsapp_messages = inquiry.whatsapp_messages.order_by(WhatsAppMessage.received_at.asc()).all()

        # Combine and sort communications into a single timeline
        timeline = []
        for email in emails:
            # Ensure timestamp is timezone-aware (assuming UTC if naive)
            ts = email.received_at
            if ts and ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            timeline.append({'type': 'email', 'data': email, 'timestamp': ts})
            
        for msg in whatsapp_messages:
            # Compare using received_at (when webhook was received)
            # Or use wa_timestamp if available and reliable?
            ts = msg.received_at 
            if ts and ts.tzinfo is None:
                 ts = ts.replace(tzinfo=timezone.utc)
            # Use wa_timestamp if received_at is missing and wa_timestamp exists
            if ts is None and msg.wa_timestamp:
                 ts = msg.wa_timestamp
                 if ts and ts.tzinfo is None:
                      ts = ts.replace(tzinfo=timezone.utc)
                     
            # Fallback if no timestamp
            if ts is None:
                 ts = inquiry.created_at # Or some other default
                 if ts and ts.tzinfo is None:
                      ts = ts.replace(tzinfo=timezone.utc)
                     
            timeline.append({'type': 'whatsapp', 'data': msg, 'timestamp': ts})

        # Sort the combined timeline by timestamp
        timeline.sort(key=lambda item: item['timestamp'] or datetime.min.replace(tzinfo=timezone.utc)) # Handle potential None timestamps

    except Exception as e:
        current_app.logger.error(f"Error fetching details for inquiry_id {inquiry_id}: {e}", exc_info=True)
        abort(500, description="Error retrieving inquiry details.")

    # Pass the inquiry object and the sorted timeline to the template
    return render_template('inquiry_detail.html', 
                           inquiry=inquiry, 
                           timeline=timeline, # Pass the unified timeline
                           user=current_user)
# --- End Inquiry Detail Route --- 