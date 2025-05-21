# Email Processing - Implementation Plan to Address Missing Emails

## 1. Problem Identification

The current system is suspected to be missing incoming emails. The root cause has been identified in the `ms_graph_service.py` module, specifically within the `fetch_new_emails_since` function. This function fetches emails from the Microsoft Graph API but limits results using the `$top=50` parameter without handling pagination. If more than 50 emails arrive between polling intervals, only the first 50 are retrieved, and the rest are missed for that polling cycle.

## 2. Proposed Solution

The solution involves modifying the email fetching mechanism to correctly handle paginated responses from the Microsoft Graph API. This will ensure all new emails are retrieved and processed.

## 3. Detailed Implementation Steps

### Step 3.1: Enhance Graph API Call Handling for Pagination

**File:** `ms_graph_service.py`

**Objective:** Modify the Graph API communication layer to automatically handle pagination for GET requests that might return multiple pages of results.

**Changes:**

1.  **Update `_make_graph_api_call` (or create a new paginated version like `_make_paginated_graph_api_call`):**
    *   This function will be responsible for making the initial API request.
    *   After receiving a response, it will check for an `@odata.nextLink` field in the JSON response.
    *   If `@odata.nextLink` exists, the function will make subsequent GET requests to this URL.
    *   It will continue fetching pages until `@odata.nextLink` is no longer present in the response.
    *   The `value` arrays from each page's response should be aggregated into a single list.
    *   The retry logic (`@retry_graph_call`) should apply to each individual page request.

    **Pseudocode for `_make_paginated_graph_api_call`:**
    ```python
    # @retry_graph_call # Apply retry to the core request function
    # def _fetch_single_page(method, endpoint, headers, params=None, json_data=None):
    #     # Actual requests.request logic here
    #     # response.raise_for_status()
    #     # return response.json()

    # def _make_paginated_graph_api_call(method, endpoint, params=None, json_data=None):
    #     _ensure_config_loaded()
    #     token = get_access_token()
    #     headers = {
    #         'Authorization': f'Bearer {token}',
    #         'Content-Type': 'application/json'
    #     }
    #
    #     all_items = []
    #     current_endpoint = endpoint # Initial endpoint
    #     current_params = params # Initial params
    #
    #     while current_endpoint:
    #         # response_json = _fetch_single_page(method, current_endpoint, headers, params=current_params, json_data=json_data)
    #         # For the first call, params are passed. For subsequent calls, current_endpoint is the nextLink which has params embedded.
    #         # So, for nextLink calls, params argument to _fetch_single_page should be None.
    #
    #         # Simplified logic for direct call within this loop for now
    #         logging.debug(f"Fetching page: {current_endpoint} with params: {current_params if current_endpoint == endpoint else 'None (params in nextLink)'}")
    #         response = requests.request(method, current_endpoint, headers=headers, params=(current_params if current_endpoint == endpoint else None), json=json_data)
    #         response.raise_for_status() # Let retry handle this if it's part of a decorated function
    #         page_data = response.json()
    #
    #         if "value" in page_data:
    #             all_items.extend(page_data["value"])
    #
    #         if "@odata.nextLink" in page_data:
    #             current_endpoint = page_data["@odata.nextLink"]
    #             current_params = None # Params are in the nextLink
    #             method = "GET" # nextLink calls are always GET
    #             json_data = None
    #         else:
    #             current_endpoint = None # No more pages
    #
    #     return {"value": all_items} # Return in a structure consistent with single-page responses
    ```
    *Consider where to best apply the `@retry_graph_call` decorator. It might be more robust on the function that fetches a single page, if `_make_paginated_graph_api_call` calls such a helper for each page.*
    *A simpler approach for now: Apply retry to `_make_graph_api_call` and let it handle single page fetches. Then, create a new wrapper function, say `get_all_pages`, which calls `_make_graph_api_call` repeatedly.*

    **Revised Plan for `_make_graph_api_call` and `fetch_new_emails_since`:**
    It's cleaner to keep `_make_graph_api_call` for single requests and have `fetch_new_emails_since` manage the pagination loop.

2.  **Modify `fetch_new_emails_since` function:**
    *   This function will now call `_make_graph_api_call` in a loop.
    *   It will start with the initial endpoint and parameters.
    *   After each call, it will check the response for `@odata.nextLink`.
    *   If `nextLink` is present, it will update the endpoint for the next call to `_make_graph_api_call` to be this `nextLink` (note: parameters should be `None` for `nextLink` calls as they are embedded in the URL).
    *   It will accumulate all emails from the `value` arrays of each page.
    *   The `$orderby: 'receivedDateTime asc'` parameter should be maintained in the initial request to ensure correct ordering.

    **Pseudocode for updated `fetch_new_emails_since`:**
    ```python
    # def fetch_new_emails_since(timestamp):
    #     # ... (initial setup: target_user, filter_query etc.) ...
    #     initial_endpoint = f"https://graph.microsoft.com/v1.0/users/{target_user}/messages"
    #     params = {
    #         '$top': 50, # Keep a reasonable page size
    #         '$select': 'id,subject,receivedDateTime,isRead,from,bodyPreview',
    #         '$filter': filter_query,
    #         '$orderby': 'receivedDateTime asc'
    #     }
    #
    #     all_new_emails = []
    #     current_endpoint_url = initial_endpoint
    #     current_params = params
    #
    #     while current_endpoint_url:
    #         logging.info(f"Fetching page of new emails from: {current_endpoint_url}")
    #         # For the first call, current_params is used.
    #         # For subsequent calls (nextLink), params are embedded in current_endpoint_url, so pass params=None
    #         data = _make_graph_api_call("GET", current_endpoint_url, params=(current_params if current_endpoint_url == initial_endpoint else None))
    #
    #         if data and "value" in data:
    #             all_new_emails.extend(data["value"])
    #
    #         if data and "@odata.nextLink" in data:
    #             current_endpoint_url = data["@odata.nextLink"]
    #             current_params = None # nextLink contains all necessary query parameters
    #         else:
    #             current_endpoint_url = None # No more pages
    #
    #     if all_new_emails:
    #         logging.info(f"Found a total of {len(all_new_emails)} new email(s) across all pages since last check.")
    #     else:
    #         logging.debug("No new emails found across all pages since last check.")
    #     return all_new_emails
    # # ... (exception handling) ...
    ```

### Step 3.2: Verify `poll_new_emails` in `app/background_tasks.py`

**File:** `app/background_tasks.py`

**Objective:** Ensure the calling function `poll_new_emails` correctly handles the potentially larger list of emails returned by the updated `fetch_new_emails_since`.

**Changes:**
*   No changes are likely needed in `poll_new_emails` itself, as it already iterates over the list returned by `fetch_new_emails_since`. The increased number of emails will be handled by its existing loop.
*   **Logging:** Confirm logging is adequate to show the total number of emails fetched *before* enqueueing begins, and the number successfully enqueued. The current logging seems sufficient.

### Step 3.3: Configuration and Environment Variables

*   No changes to configuration or environment variables are anticipated for this fix, as it's an internal logic change.
*   The `POLL_INTERVAL_SECONDS` and `MS_GRAPH_MAILBOX_USER_ID` remain relevant.

### Step 3.4: Testing Strategy

1.  **Unit Tests (ms_graph_service.py):**
    *   Mock `requests.request` within `_make_graph_api_call`.
    *   Test `fetch_new_emails_since`:
        *   Scenario 1: API returns no emails.
        *   Scenario 2: API returns one page of emails (e.g., 10 emails, less than `$top`).
        *   Scenario 3: API returns multiple pages of emails (e.g., 70 emails if `$top=50`, requiring two pages). Mock responses to include `@odata.nextLink`.
        *   Scenario 4: API call fails (ensure retry logic is triggered and error is handled).
2.  **Integration Tests (app/background_tasks.py & ms_graph_service.py):**
    *   Test `poll_new_emails` function.
    *   Mock `ms_graph_service.fetch_new_emails_since` to simulate different numbers of emails returned (including multi-page scenarios).
    *   Verify that all emails are correctly classified (mock `classify_email_intent`) and enqueued (mock `current_email_queue.enqueue`).
    *   Verify `last_checked_timestamp` behavior on success and failure.
3.  **End-to-End Testing (Staging/Test Environment):**
    *   If possible, set up a test mailbox.
    *   Send a batch of emails exceeding the `$top` limit (e.g., >50 emails) to the test mailbox shortly before the poller is expected to run.
    *   Observe logs to confirm all emails are fetched across multiple pages.
    *   Verify that jobs for all emails are created in the RQ queue.
    *   Verify that all emails are processed and appear in the database.

## 4. Potential Risks and Mitigation

*   **Increased API Calls:** Handling pagination will lead to more Graph API calls if many emails arrive.
    *   **Mitigation:** Microsoft Graph API has throttling limits. The existing retry logic with exponential backoff in `_make_graph_api_call` should help manage this. Ensure the `$top` parameter remains at a reasonable level (e.g., 50-100) to balance the number of items per request vs. the number of requests.
*   **Increased Processing Time for `poll_new_emails`:** If a very large number of emails (e.g., hundreds) are fetched in one go, the `poll_new_emails` function might take longer to classify and enqueue them all.
    *   **Mitigation:** This is generally acceptable as it's a background task. The enqueueing step itself is quick. The actual email processing is done by RQ workers. If classification becomes a bottleneck, it might need optimization or to be moved into the RQ job itself (though current pre-classification seems reasonable).
*   **Complex State Management in `fetch_new_emails_since`:** The loop for pagination adds complexity.
    *   **Mitigation:** Clear logic and logging, as outlined in the pseudocode, are important. Thorough unit testing of this function is crucial.

## 5. Rollback Plan

*   The changes are primarily within `ms_graph_service.py`.
*   If issues arise, revert `ms_graph_service.py` to the previous version from version control.
*   No database schema changes or complex data migrations are involved, making rollback relatively straightforward.

This plan addresses the core issue of missing emails by implementing proper pagination. 