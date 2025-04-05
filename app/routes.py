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

    # Pass the inquiries list to the template
    return render_template('dashboard.html', user=current_user, inquiries=inquiries)

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

@main_bp.route('/email/<string:graph_id>/edit', methods=['GET'])
@login_required
def edit_extracted_data_form(graph_id):
    """Display the form to edit extracted data for a specific email."""
    # Fetch the email and its extracted data. Use first_or_404 for simplicity.
    email = Email.query.options(
        db.joinedload(Email.extracted_data)
    ).filter_by(graph_id=graph_id).first_or_404()

    if not email.extracted_data:
        flash(f"No extracted data found for email {graph_id} to edit.", "warning")
        return redirect(url_for('.email_detail', graph_id=graph_id))

    # Pass the ExtractedData object to the template
    return render_template('edit_extracted_data.html', 
                           extracted_data=email.extracted_data, 
                           email_subject=email.subject, # Pass subject for context
                           user=current_user)

@main_bp.route('/extracted_data/<int:data_id>/update', methods=['POST'])
@login_required
def update_extracted_data(data_id):
    """Handle the submission of the edited extracted data form."""
    # Find the specific ExtractedData record
    data_to_update = ExtractedData.query.get_or_404(data_id)
    original_email_graph_id = data_to_update.email_graph_id # Store before potential changes

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

        # 6. Redirect back to detail page with flash message
        flash("Extracted data updated successfully.", "success")
        return redirect(url_for('.email_detail', graph_id=original_email_graph_id))

    except SQLAlchemyError as e:
        db.session.rollback() # Rollback in case of DB error
        current_app.logger.error(f"Database error updating ExtractedData ID {data_id}: {e}")
        flash("Database error occurred while saving changes.", "danger")
    except Exception as e:
        db.session.rollback() # Rollback for any other unexpected errors
        current_app.logger.error(f"Unexpected error updating ExtractedData ID {data_id}: {e}")
        flash("An unexpected error occurred.", "danger")

    # If any error occurred, redirect back to the edit form or detail page
    # Redirecting to detail page to avoid losing context if form re-render is complex
    return redirect(url_for('.email_detail', graph_id=original_email_graph_id))

# --- End Edit Routes --- 