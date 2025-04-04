from app import create_app

# Call the app factory function to create the Flask app instance
app = create_app()

# The following block is not needed when running with Gunicorn
# Gunicorn directly uses the 'app' object above.
# if __name__ == "__main__":
#     app.run(host="0.0.0.0", port=5000, debug=True)
