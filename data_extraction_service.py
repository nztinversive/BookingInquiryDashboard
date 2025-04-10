import os
import re
import json
import logging
import traceback
from openai import OpenAI
from bs4 import BeautifulSoup

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- OpenAI Client Initialization ---
openai_client = None
try:
    api_key = os.environ.get("OPEN_API_KEY")
    if api_key:
        openai_client = OpenAI(api_key=api_key)
        logging.info("OpenAI client initialized successfully.")
    else:
        logging.warning("OPEN_API_KEY secret not found. OpenAI extraction will be disabled.")
except Exception as e:
    logging.error(f"Failed to initialize OpenAI client: {e}")
    openai_client = None
# --- ---

# --- Intent Classification --- 
def classify_email_intent(subject, body_preview):
    """Classifies email intent using OpenAI."""
    if not openai_client:
        logging.warning("OpenAI client not available. Cannot classify intent.")
        return "unknown" # Default if client fails
    if not subject and not body_preview:
        logging.warning("No subject or body preview for intent classification.")
        return "unknown"

    logging.info("Calling OpenAI API for intent classification...")
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
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini", # Use a fast and capable model
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_content}
            ],
            temperature=0.1, # Very low temperature for focused classification
            max_tokens=20, # Limit response length to just the label
            timeout=30 # Shorter timeout for classification
        )

        intent_label = response.choices[0].message.content.strip().lower()
        logging.info(f"Received intent classification from OpenAI: {intent_label}")
        
        # Basic validation of the label
        valid_intents = ['inquiry', 'spam', 'solicitation', 'out_of_office', 'undeliverable', 'confirmation', 'personal', 'other']
        if intent_label in valid_intents:
            return intent_label
        else:
            logging.warning(f"OpenAI returned an unexpected intent label: '{intent_label}'. Defaulting to 'other'.")
            return "other"

    except Exception as openai_e:
        logging.error(f"Error during OpenAI intent classification: {openai_e}", exc_info=True)
        return "unknown" # Return unknown on error
# --- ---

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


def extract_data_with_openai(content):
    """Extracts travel data using OpenAI API."""
    if not openai_client:
        logging.warning("OpenAI client not available. Skipping OpenAI extraction.")
        return None
    if not content:
        logging.warning("No content provided for OpenAI extraction.")
        return None

    logging.info("Calling OpenAI API for data extraction...")
    system_message = """You are a specialized AI assistant for extracting travel insurance data from emails and documents.
Your task is to accurately identify and extract the following fields:
- first_name: The primary traveler's first name
- last_name: The primary traveler's last name
- home_address: Their full home address (Street, City, State, Zip)
- date_of_birth: Primary traveler's date of birth (try to standardize to YYYY-MM-DD if possible, otherwise use format found)
- travel_start_date: Trip start date (try to standardize to YYYY-MM-DD if possible, otherwise use format found)
- travel_end_date: Trip end date (try to standardize to YYYY-MM-DD if possible, otherwise use format found)
- trip_cost: The total cost of the trip (numeric value if possible, e.g., 1234.56)
- trip_destination: The primary destination(s) of the trip (e.g., "Paris, France", "Italy and Greece"). Return as a string.
- email: Their primary email address
- phone_number: Their primary phone number
- travelers: An array containing ALL travelers mentioned (including the primary one). **Crucially, for EACH traveler in this array, include their first_name, last_name, and date_of_birth.** Standardize the date_of_birth to YYYY-MM-DD if possible; otherwise, use the format found. If a specific traveler's DOB is not mentioned, use null for their date_of_birth field.

Look carefully for ALL travelers and their associated dates of birth. Return only a valid JSON object with these exact keys. If a top-level value cannot be found, use null. Ensure 'travelers' is an array.
Be meticulous. Double-check extracted information against the source text. Do not add explanations or fields not requested."""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": f"Extract the required information from this text:\n\n---\n{content}\n---"}
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=90
        )

        extracted_str = response.choices[0].message.content
        logging.info("Received response from OpenAI.")

        # Basic validation and parsing
        if not extracted_str or extracted_str.strip() == '{}':
             logging.warning("OpenAI returned empty or minimal response.")
             return None

        ai_extracted_json = json.loads(extracted_str)
        logging.info("OpenAI extraction successful.")
        return ai_extracted_json

    except json.JSONDecodeError as json_e:
        logging.error(f"Failed to parse JSON response from OpenAI: {json_e}")
        logging.error(f"OpenAI Raw Response: {extracted_str}")
        return None
    except Exception as openai_e:
        logging.error(f"Error during OpenAI extraction: {openai_e}", exc_info=True)
        return None


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
            "phone_number", "travelers", "trip_destination"
        ]
        for key in expected_keys:
             if key not in final_data:
                 final_data[key] = None
        if "travelers" not in final_data: # Ensure travelers is at least an empty list
            final_data["travelers"] = []


    # 4. Calculate Cost Per Traveler
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


    logging.info(f"Extraction finished. Source: {source}")
    return final_data, source 