from flask import Blueprint, render_template, current_app, Response, abort, url_for, request, redirect, flash # Added request, redirect, flash
from flask_login import login_required, current_user
from sqlalchemy.exc import SQLAlchemyError # Import specific exception
from sqlalchemy.orm import joinedload, selectinload # Added selectinload
from .models import Email, ExtractedData, User, Inquiry # Added Inquiry model
from . import db # Import the db object
import json # Import json for potential type casting

# Create a Blueprint for the main application routes
# Remove explicit static_folder here to rely on app-level static handling
main_bp = Blueprint('main', __name__,
                    template_folder='templates')

@main_bp.route('/')
@main_bp.route('/dashboard') # Add specific dashboard route if needed
@login_required # Assuming dashboard requires login
def dashboard():
    """Render the main dashboard page, now based on Inquiries."""
    try:
        # Query Inquiry records, loading related data
        inquiries = Inquiry.query.options(
            # selectinload(Inquiry.emails),  # REMOVED: Incompatible with lazy='dynamic'
            joinedload(Inquiry.extracted_data) # Keep loading the one-to-one extracted data
        ).order_by(Inquiry.updated_at.desc(), Inquiry.created_at.desc()).all()
    except Exception as e:
        current_app.logger.error(f"Error fetching inquiries for dashboard: {e}", exc_info=True) # Log full traceback
        inquiries = [] # Return an empty list on error
        flash("Error loading dashboard data.", "danger") # Inform user

    # Prepare data for the template, including the latest email for each inquiry
    dashboard_data = []
    for inquiry in inquiries:
        latest_email = None
        try:
            # Query the latest email associated with this inquiry
            # Assuming Inquiry.emails is a dynamic relationship
            latest_email = inquiry.emails.order_by(Email.received_at.desc()).first()
        except Exception as email_query_err:
            # Log if fetching the latest email fails for a specific inquiry
            current_app.logger.error(f"Error fetching latest email for Inquiry {inquiry.id}: {email_query_err}")
        
        dashboard_data.append({
            'inquiry': inquiry,
            'latest_email': latest_email
        })

    # Pass the prepared data list to the template
    return render_template('dashboard.html', user=current_user, dashboard_items=dashboard_data)

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
        # Log unexpected errors during query
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
        return redirect(url_for('.dashboard')) 

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
             return redirect(url_for('.dashboard'))

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
        return redirect(url_for('.dashboard')) 

# --- End Edit Routes --- 

# --- New Route for Inquiry Details ---
@main_bp.route('/inquiry/<int:inquiry_id>')
@login_required
def inquiry_detail(inquiry_id):
    """Show details for a specific Inquiry, its data, and associated emails."""
    try:
        # Query for the inquiry, eagerly load extracted data
        # We can't eager load 'emails' because it's a dynamic relationship
        inquiry = Inquiry.query.options(
            joinedload(Inquiry.extracted_data)
            # selectinload(Inquiry.emails) # REMOVED: Incompatible with lazy='dynamic'
        ).get_or_404(inquiry_id)

    except Exception as e:
        # Log unexpected errors during query
        current_app.logger.error(f"Error fetching inquiry details for inquiry_id {inquiry_id}: {e}")
        abort(500, description="Error retrieving inquiry details.") # Internal server error

    # Render a new template, passing the inquiry object
    return render_template('inquiry_detail.html', inquiry=inquiry, user=current_user)
# --- End Inquiry Detail Route --- 