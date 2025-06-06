import os
import re
import json
import logging
import traceback
from openai import OpenAI, OpenAIError, RateLimitError, APITimeoutError, APIConnectionError
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# Tenacity imports
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- OpenAI Retry Configuration ---
# Define which exceptions should trigger a retry for OpenAI
retry_openai_call = retry(
    stop=stop_after_attempt(4), # Retry 3 times (4 attempts total)
    wait=wait_exponential(multiplier=1, min=2, max=30), # Wait 2s, 4s, 8s (max 30s)
    retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIConnectionError, OpenAIError)) # Retry on rate limits, timeouts, connection errors, and general API errors (like 5xx)
    # Note: OpenAIError is broad, might catch some non-transient errors. Adjust if needed.
)

# --- OpenAI Client (initialized by configure_openai_client) ---
openai_client = None

# --- Configuration Function (called from app factory) ---
def configure_openai_client(config):
    """Initializes the OpenAI client using API key from Flask app config."""
    global openai_client
    api_key = config.get("OPENAI_API_KEY")
    if api_key:
        try:
            openai_client = OpenAI(api_key=api_key)
            # Optional: Make a simple test call to ensure the key is valid?
            # E.g., openai_client.models.list() 
            # Be mindful of cost/rate limits if doing this.
            logging.info("OpenAI client initialized successfully.")
            return True
        except Exception as e:
            logging.error(f"Failed to initialize OpenAI client with provided key: {e}", exc_info=True)
            openai_client = None
            return False
    else:
        logging.warning("OPENAI_API_KEY not found in configuration. OpenAI features will be disabled.")
        openai_client = None
        return False

# --- Intent Classification --- 
@retry_openai_call
def _call_openai_for_intent(system_message, user_content):
    """Internal function to make the actual OpenAI call for intent classification, with retry logic."""
    if not openai_client:
        raise RuntimeError("OpenAI client not initialized.") # Should not happen if called correctly
    
    logging.debug("Making OpenAI call for intent...")
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1,
            max_tokens=20,
            timeout=30
        )
        # Log attempt details (useful for retry debugging)
        attempt_number = _call_openai_for_intent.retry.statistics.get('attempt_number', 1)
        if attempt_number > 1:
            logging.warning(f"OpenAI intent call attempt {attempt_number}")
            
        logging.debug("OpenAI intent call successful.")
        return response.choices[0].message.content.strip().lower()
    except (RateLimitError, APITimeoutError, APIConnectionError, OpenAIError) as openai_e:
        attempt_number = _call_openai_for_intent.retry.statistics.get('attempt_number', 1)
        logging.warning(f"OpenAI intent call failed (attempt {attempt_number}): {openai_e}")
        raise # Re-raise for tenacity
    except Exception as e:
        # Catch other unexpected errors
        logging.error(f"Unexpected error during OpenAI intent call: {e}", exc_info=True)
        raise # Re-raise to signal failure

def classify_email_intent(subject, body_preview):
    """Classifies email intent using OpenAI (calls internal retry function)."""
    if not openai_client:
        logging.warning("OpenAI client not available for intent classification.")
        return "unknown" # Default if client fails or not configured
    if not subject and not body_preview:
        logging.warning("No subject or body preview for intent classification.")
        return "unknown"

    logging.info("Preparing OpenAI call for intent classification...")
    system_message = """You are an AI assistant classifying emails for a travel insurance agency. 
Classify the intent of the following email based on its subject and body preview.
Possible intents are:
- 'inquiry': A customer is asking for a quote, pricing, information about travel insurance, or providing details for a quote.
- 'spam': Unsolicited commercial email, phishing, or irrelevant marketing.
- 'solicitation': A business is trying to sell services *to* the agency (e.g., marketing, web design, SEO).
- 'out_of_office': An automatic reply indicating someone is away.
- 'undeliverable': A bounce-back message about a failed email delivery.
- 'confirmation': A confirmation of a booking or action (less common for incoming).
- 'personal': Non-business related personal email.
- 'other': The email doesn't fit clearly into the above categories.

Respond ONLY with the single intent label (e.g., 'inquiry', 'spam', 'solicitation')."""

    user_content = f"Subject: {subject}\n\nBody Preview (first 100 chars):\n{body_preview[:100]}...\n\nIntent:"

    try:
        # Call the internal function that has the retry logic
        intent_label = _call_openai_for_intent(system_message, user_content)
        logging.info(f"Received intent classification from OpenAI: {intent_label}")
        
        # Basic validation of the label
        valid_intents = ['inquiry', 'spam', 'solicitation', 'out_of_office', 'undeliverable', 'confirmation', 'personal', 'other']
        if intent_label in valid_intents:
            return intent_label
        else:
            logging.warning(f"OpenAI returned an unexpected intent label: '{intent_label}'. Defaulting to 'other'.")
            return "other"

    except Exception as e: # Catch exceptions after retries have failed
        logging.error(f"Failed OpenAI intent classification after retries: {e}", exc_info=False) # Don't need full trace here
        return "unknown" # Return unknown on final failure

def get_text_from_html(html_content):
    """Extracts plain text from HTML content."""
    if not html_content:
        return ""
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        # Improve text extraction: join lines, remove excessive whitespace
        lines = (line.strip() for line in soup.get_text().splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        return text
    except Exception as e:
        logging.error(f"Error parsing HTML: {e}")
        return "" # Return empty string on parsing error

def attempt_local_extraction(content):
    """
    Attempt to extract travel-related data using regex patterns.
    """
    # Initialize the result dictionary with default values
    result = {
        "first_name": None,
        "last_name": None,
        "home_address": None,
        "date_of_birth": None,
        "travel_start_date": None,
        "travel_end_date": None,
        "trip_cost": None,
        "email": None,
        "phone_number": None,
        "trip_destination": None,
        "initial_trip_deposit_date": None,
        "origin": None,
        "travelers": []
    }

    # Simplified regex patterns (adjust as needed)
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    phone_pattern = r'\b(?:\+\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'
    # More robust date pattern allowing different separators and formats
    date_pattern = r'\b(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[.,]?\s+\d{1,2}[.,]?\s+\d{4})\b|\b(?:\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b|\b(?:\d{4}[-/]\d{2}[-/]\d{2})\b'
    cost_pattern = r'\$(?: )?([\d,]+\.?\d{0,2})\b' # Capture the number part
    # Very basic name pattern (likely needs improvement)
    name_pattern = r'\b(?:Mr|Mrs|Ms|Dr)\.?\s+([A-Z][a-z]+(?:-[A-Z][a-z]+)?)\s+([A-Z][a-z]+(?:-[A-Z][a-z]+)?)\b'
    # Basic address pattern (highly variable, difficult with regex)
    address_pattern = r'\b\d+\s+[A-Za-z0-9\s.,]+(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln)[.,]?\s+[A-Za-z\s]+(?:,)?\s*[A-Z]{2}\s*\d{5}(?:-\d{4})?\b'
    # Basic destination pattern (very limited)
    destination_pattern = r'\b(?:traveling|going|trip)\s+to\s+([A-Z][a-zA-Z\s,]+)\b'
    # Basic deposit date pattern (looks for dates near keywords)
    deposit_date_pattern = r'(?:deposit|paid|booked)(?: on)?[:\s]*(' + date_pattern + r')' # Uses the existing date pattern
    # Basic origin pattern (very unreliable, likely needs OpenAI)
    # Updated to look for US states (abbreviations or names) near keywords
    us_states_pattern = r'(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|[A-Z]{2})' # Full names or 2-letter caps
    origin_pattern = r'(?:departing|leaving|coming)\s+from\s+((?:[A-Za-z\s]+,\s*)?' + us_states_pattern + r')' # Optional city/context before state

    try:
        emails = re.findall(email_pattern, content)
        if emails: result["email"] = emails[0]

        phones = re.findall(phone_pattern, content)
        if phones: result["phone_number"] = phones[0]

        dates = re.findall(date_pattern, content, re.IGNORECASE)
        # Basic date assignment logic (needs context for accuracy)
        if len(dates) >= 2:
            result["travel_start_date"] = dates[0]
            result["travel_end_date"] = dates[1]
        elif len(dates) == 1:
             # Could be DOB or a single travel date - ambiguous
             # For now, assign to DOB if no travel dates yet
             if not result["travel_start_date"]:
                 result["date_of_birth"] = dates[0]


        costs = re.findall(cost_pattern, content)
        if costs: result["trip_cost"] = f"${costs[0]}" # Add back the dollar sign

        addresses = re.findall(address_pattern, content, re.IGNORECASE)
        if addresses: result["home_address"] = addresses[0]

        # Attempt to find destination (simple case)
        destinations = re.findall(destination_pattern, content, re.IGNORECASE)
        if destinations: result["trip_destination"] = destinations[0].strip()

        # Attempt to find initial deposit date
        deposit_dates = re.findall(deposit_date_pattern, content, re.IGNORECASE)
        # Findall captures groups within the pattern, hence deposit_dates might be list of tuples/strings depending on date_pattern structure
        # We need the actual date string captured by the inner date_pattern group
        if deposit_dates:
            # Extract the first matched date string (handling potential tuple structure if date_pattern has groups)
            first_match = deposit_dates[0]
            actual_date = first_match[0] if isinstance(first_match, tuple) else first_match
            result["initial_trip_deposit_date"] = actual_date.strip()

        # Attempt to find origin (simple case, focusing on US States)
        origin_matches = re.search(origin_pattern, content, re.IGNORECASE)
        if origin_matches:
            # group(1) should capture the optional city + state part
            actual_origin = origin_matches.group(1)
            if actual_origin:
                result["origin"] = actual_origin.strip()

        names = re.findall(name_pattern, content)
        primary_traveler_added = False
        for fname, lname in names:
            result["travelers"].append({
                "first_name": fname,
                "last_name": lname,
                "date_of_birth": None # Cannot reliably link DOB with regex
            })
            if not primary_traveler_added:
                result["first_name"] = fname
                result["last_name"] = lname
                # Try assigning the found DOB to the primary traveler
                if result["date_of_birth"] and len(dates) == 1:
                     result["travelers"][0]["date_of_birth"] = result["date_of_birth"]
                primary_traveler_added = True

        # If only one date was found and assigned as DOB, but we have travelers,
        # ensure the main DOB field is also populated if primary traveler info exists.
        if not result["date_of_birth"] and result["first_name"] and result["travelers"] and result["travelers"][0].get("date_of_birth"):
             result["date_of_birth"] = result["travelers"][0]["date_of_birth"]


    except Exception as e:
        logging.error(f"Error during local extraction: {e}")
        # Return partially filled dict or empty dict? Return what we have.
    return result

# Apply retry decorator to the function making the API call for data extraction
@retry_openai_call
def _call_openai_for_extraction(system_message, user_content):
    """Internal function to make the actual OpenAI call for data extraction, with retry logic."""
    if not openai_client:
        raise RuntimeError("OpenAI client not initialized.")
        
    logging.debug("Making OpenAI call for extraction...")
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o", # Use a more powerful model for structured extraction
            response_format={ "type": "json_object" }, # Request JSON output
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_content}
            ],
            temperature=0.2, # Low temperature for factual extraction
            max_tokens=1500, # Allow more tokens for potentially larger JSON output
            timeout=120 # Longer timeout for potentially complex extraction
        )
        # Log attempt details
        attempt_number = _call_openai_for_extraction.retry.statistics.get('attempt_number', 1)
        if attempt_number > 1:
            logging.warning(f"OpenAI extraction call attempt {attempt_number}")
            
        logging.debug("OpenAI extraction call successful.")
        # Parse the JSON response
        extracted_json = json.loads(response.choices[0].message.content)
        return extracted_json
    except (RateLimitError, APITimeoutError, APIConnectionError, OpenAIError) as openai_e:
        attempt_number = _call_openai_for_extraction.retry.statistics.get('attempt_number', 1)
        logging.warning(f"OpenAI extraction call failed (attempt {attempt_number}): {openai_e}")
        raise # Re-raise for tenacity
    except json.JSONDecodeError as json_err:
        logging.error(f"Failed to decode JSON response from OpenAI: {json_err}")
        # Log the raw response content for debugging if possible (careful with length)
        try:
             raw_content = response.choices[0].message.content
             logging.error(f"Raw OpenAI response content: {raw_content[:1000]}...")
        except Exception:
             logging.error("Could not retrieve raw response content.")
        raise # Re-raise JSON error as it indicates a problem
    except Exception as e:
        # Catch other unexpected errors
        logging.error(f"Unexpected error during OpenAI extraction call: {e}", exc_info=True)
        raise # Re-raise to signal failure

def extract_data_with_openai(content):
    """Extracts travel data using OpenAI API (calls internal retry function)."""
    if not openai_client:
        logging.warning("OpenAI client not available. Skipping OpenAI extraction.")
        return None
    if not content:
        logging.warning("No content provided for OpenAI extraction.")
        return None

    logging.info("Preparing OpenAI call for data extraction...")
    system_message = """You are a specialized AI assistant for extracting travel insurance data from emails and documents.
Your task is to accurately identify and extract the following fields and return them ONLY as a valid JSON object.

IMPORTANT DATE HANDLING RULES:
- All dates must be standardized to YYYY-MM-DD format
- For travel dates (travel_start_date, travel_end_date), if no year is specified, assume the next occurrence of that date (2025 or 2026 if needed)
- Travel dates should NEVER be in the past unless explicitly specified
- For birth dates, use the year if provided, otherwise return null
- Deposit dates should be reasonable relative to travel dates

Fields:
- first_name: The primary traveler's first name (string or null).
- last_name: The primary traveler's last name (string or null).
- home_address: Their full home address (Street, City, State, Zip) (string or null).
- date_of_birth: Primary traveler's date of birth (standardize to YYYY-MM-DD, string or null).
- travel_start_date: Trip start date (standardize to YYYY-MM-DD, assume future year if not specified, string or null).
- travel_end_date: Trip end date (standardize to YYYY-MM-DD, assume future year if not specified, string or null).
- trip_cost: The total cost of the trip (numeric, e.g., 1234.56 or null).
- trip_destination: The primary destination(s) (string, e.g., "Paris, France" or null).
- email: Their primary email address (string or null).
- phone_number: Their primary phone number (string or null).
- initial_trip_deposit_date: The date the first payment/deposit was made (standardize YYYY-MM-DD, string or null).
- origin: Departure location, typically US State (string, e.g., "California", "NY", or null).
- travelers: An array containing ALL travelers mentioned (including primary). For EACH traveler object in the array, include: first_name (string), last_name (string), date_of_birth (standardize YYYY-MM-DD, string or null). Example: [{"first_name": "John", "last_name": "Doe", "date_of_birth": "1985-03-15"}, {"first_name": "Jane", "last_name": "Doe", "date_of_birth": null}]. If no travelers mentioned, return an empty array [].

Return ONLY the JSON object. Do not include explanations or apologies.
"""

    # Truncate content if it's excessively long to avoid high token usage
    MAX_CONTENT_LENGTH = 15000 # Adjust as needed (approx ~4k tokens)
    if len(content) > MAX_CONTENT_LENGTH:
        logging.warning(f"Content length ({len(content)}) exceeds limit ({MAX_CONTENT_LENGTH}), truncating for OpenAI.")
        content = content[:MAX_CONTENT_LENGTH] + "... [TRUNCATED]"

    user_content = f"Extract the required data fields from the following text:\n\n---\n{content}\n---"

    try:
        # Call the internal function with retry logic
        extracted_data = _call_openai_for_extraction(system_message, user_content)
        logging.info("Successfully extracted data using OpenAI.")
        # Basic validation: ensure it's a dictionary
        if isinstance(extracted_data, dict):
            return extracted_data
        else:
            logging.error(f"OpenAI returned unexpected data type: {type(extracted_data)}")
            return None # Indicate failure

    except Exception as e:
        logging.error(f"Failed OpenAI data extraction after retries: {e}", exc_info=False) 
        return None # Return None on final failure

def extract_travel_data(email_body_html):
    """
    Orchestrates data extraction: gets text, runs local, runs OpenAI, merges.
    Calculates cost per traveler.
    """
    logging.info("Starting travel data extraction process...")
    text_content = get_text_from_html(email_body_html)
    if not text_content:
        logging.warning("No text content could be extracted from HTML body.")
        return {}, 'none' # Return empty dict and 'none' source

    # 1. Local Extraction
    local_results = {}
    try:
        local_results = attempt_local_extraction(text_content)
        logging.info("Local extraction performed.")
    except Exception as local_e:
        logging.error(f"Local extraction failed: {local_e}", exc_info=True)
        # Continue even if local fails

    # 2. OpenAI Extraction
    openai_results = None
    try:
        openai_results = extract_data_with_openai(text_content)
    except Exception as openai_e:
        logging.error(f"OpenAI extraction call failed: {openai_e}", exc_info=True)
        # Continue, rely on local results

    # 3. Merge Results (Prefer OpenAI for non-null values)
    final_data = local_results.copy()
    source = 'local' if any(v is not None and v != [] for v in local_results.values()) else 'none'

    if openai_results:
        source = 'openai' if source == 'none' else 'combined'
        for key, value in openai_results.items():
            # Overwrite local if OpenAI found a non-null/non-empty value,
            # or if the key didn't exist locally.
            # Special handling for travelers array: overwrite if OpenAI found any travelers.
            if key == "travelers":
                 if isinstance(value, list) and len(value) > 0:
                     final_data[key] = value
                 elif key not in final_data: # Add empty list if not present locally
                      final_data[key] = []
            elif value is not None and value != "":
                final_data[key] = value
            elif key not in final_data: # Ensure all keys from OpenAI are present, even if null
                 final_data[key] = None # Explicitly set null if OpenAI returned null and key wasn't local


        # Ensure all expected keys exist in the final dict, adding null if missing
        expected_keys = [
            "first_name", "last_name", "home_address", "date_of_birth",
            "travel_start_date", "travel_end_date", "trip_cost", "email",
            "phone_number", "travelers", "trip_destination",
            "initial_trip_deposit_date", "origin"
        ]
        for key in expected_keys:
             if key not in final_data:
                 final_data[key] = None
        if "travelers" not in final_data: # Ensure travelers is at least an empty list
            final_data["travelers"] = []


    # 4. Fallback for Initial Trip Deposit Date
    if not final_data.get("initial_trip_deposit_date"):
        start_date_str = final_data.get("travel_start_date")
        if start_date_str:
            logging.info(f"Initial deposit date missing. Attempting fallback using start date: {start_date_str}")
            parsed_start_date = None
            # Attempt parsing common date formats
            formats_to_try = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"]
            for fmt in formats_to_try:
                try:
                    # Handle potential time components if OpenAI added them
                    date_only_str = start_date_str.split('T')[0] 
                    parsed_start_date = datetime.strptime(date_only_str, fmt)
                    break # Stop on first successful parse
                except ValueError:
                    continue # Try next format

            if parsed_start_date:
                try:
                    fallback_deposit_date = parsed_start_date - timedelta(days=7)
                    final_data["initial_trip_deposit_date"] = fallback_deposit_date.strftime("%Y-%m-%d")
                    logging.info(f"Successfully calculated fallback deposit date: {final_data['initial_trip_deposit_date']}")
                except Exception as calc_err:
                    logging.warning(f"Error calculating fallback deposit date from {parsed_start_date}: {calc_err}")
            else:
                logging.warning(f"Could not parse travel_start_date '{start_date_str}' to calculate fallback deposit date.")
        else:
            logging.info("Initial deposit date missing, and no travel start date found for fallback.")

    # 5. Calculate Cost Per Traveler
    cost_per_traveler = None
    try:
        raw_cost = final_data.get("trip_cost")
        travelers = final_data.get("travelers", [])
        num_travelers = len(travelers) if isinstance(travelers, list) else 0

        if raw_cost and num_travelers > 0:
            # Attempt to clean and convert cost to float
            cost_str = str(raw_cost).strip()
            # Remove common currency symbols and commas
            cost_str = re.sub(r'[$,€£¥]', '', cost_str)
            cost_str = cost_str.replace(',', '')
            cost_numeric = float(cost_str)
            cost_per_traveler = round(cost_numeric / num_travelers, 2)
            logging.info(f"Calculated cost per traveler: {cost_per_traveler} ({cost_numeric} / {num_travelers})")
        elif raw_cost:
             logging.warning(f"Trip cost '{raw_cost}' found, but number of travelers is 0. Cannot calculate cost per traveler.")
        # else: cost or travelers missing, handled by initialization

    except (ValueError, TypeError) as calc_err:
        logging.warning(f"Could not calculate cost per traveler from cost '{raw_cost}': {calc_err}")
        cost_per_traveler = None # Ensure it's None if calculation fails

    final_data["cost_per_traveler"] = cost_per_traveler

    # 6. Post-process travel dates to ensure they're not in the past
    from datetime import datetime, timedelta
    current_date = datetime.now().date()
    
    for date_field in ['travel_start_date', 'travel_end_date']:
        date_str = final_data.get(date_field)
        if date_str:
            try:
                # Parse the date
                parsed_date = None
                formats_to_try = ["%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y"]
                for fmt in formats_to_try:
                    try:
                        date_only_str = date_str.split('T')[0]
                        parsed_date = datetime.strptime(date_only_str, fmt).date()
                        break
                    except ValueError:
                        continue
                
                if parsed_date and parsed_date < current_date:
                    # Date is in the past, adjust to next year
                    next_year_date = parsed_date.replace(year=current_date.year + 1)
                    final_data[date_field] = next_year_date.strftime("%Y-%m-%d")
                    logging.info(f"Adjusted {date_field} from past date {date_str} to future date {final_data[date_field]}")
                elif parsed_date:
                    # Ensure consistent formatting
                    final_data[date_field] = parsed_date.strftime("%Y-%m-%d")
                    
            except Exception as date_err:
                logging.warning(f"Error processing {date_field} '{date_str}': {date_err}")

    logging.info(f"Extraction finished. Source: {source}")
    return final_data, source 