from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from datetime import timezone # Import timezone
 
# Initialize extensions here
db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate() 
# Configure scheduler to use UTC timezone to avoid pickling issues with ZoneInfo
scheduler = BackgroundScheduler(timezone=timezone.utc) # Default to BackgroundScheduler 