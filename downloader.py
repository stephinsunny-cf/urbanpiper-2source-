import sys
import json
import os
import time
import requests
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(__name__)

try:
    import config
except ImportError:
    log.error("config.py not found!")
    sys.exit(1)


# ==============================================================================
#  SESSION MANAGEMENT
# ==============================================================================

def load_cached_session():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.SESSION_FILE)
    if os.path.exists(path):
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, dict) and "cookies" in data:
                log.info("Loaded cached session (token present: {}).".format(
                    bool(data.get("token"))))
                return data
        except Exception as e:
            log.warning("Could not load session cache: {}".format(e))
    return None


def save_session(session):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.SESSION_FILE)
    with open(path, "w") as f:
        json.dump(session, f)
    log.info("Session saved (token present: {}).".format(bool(session.get("token"))))


def delete_session():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), config.SESSION_FILE)
    if os.path.exists(path):
        os.remove(path)
        log.info("Cached session deleted.")


# ==============================================================================
#  PLAYWRIGHT LOGIN
# ==============================================================================

def login_and_get_session():
    from playwright.sync_api import sync_playwright
    log.info("Starting browser login...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        captured_token = {"value": None}

        def handle_response(response):
            if "atlas-backend.svc.urbanpiper.com/graphql" not in response.url:
                return
            try:
                body = response.json()
                data = (body.get("data") or {})
                login_data = data.get("loginAuthServiceAccess", {})
                if login_data and login_data.get("status", {}).get("success"):
                    token = login_data.get("token")
                    if token:
                        captured_token["value"] = token
                        log.info("Captured JWT from authServiceLogin response.")
            except Exception:
                pass

        page.on("response", handle_response)

        try:
            page.goto(config.ATLAS_LOGIN_URL, wait_until="commit", timeout=30000)
            log.info("Initial URL: {}".format(page.url))

            email_sel = (
                "input[type=email], input[type=text], input[name=email], "
                "input[name=username], input[placeholder*=mail i], "
                "input[placeholder*=mobile i], input[placeholder*=email i], "
                "input:not([type=hidden])"
            )
            page.wait_for_selector(email_sel, state="visible", timeout=45000)
            page.locator(email_sel).first.fill(config.UP_EMAIL)
            page.locator("button[type=submit], input[type=submit]").first.click()

            page.wait_for_selector("input[type=password]", state="visible", timeout=30000)
            page.locator("input[type=password]").first.fill(config.UP_PASSWORD)
            page.locator("button[type=submit], input[type=submit]").first.click()

            try:
                page.wait_for_url("**/business**", timeout=8000)
                business_name = os.environ.get("BUSINESS_NAME", "Eatfit - MOC")
                biz_locator = page.locator(f"text={business_name}")
                biz_locator.wait_for(state="visible", timeout=10000)
                biz_locator.click()
            except Exception:
                pass

            page.wait_for_function(
                "window.location.hostname === 'atlas.urbanpiper.com' "
                "&& window.location.pathname !== '/login'",
                timeout=45000
            )
            log.info("Login successful! URL: {}".format(page.url))
            page.wait_for_timeout(3000)

            if not captured_token["value"]:
                try:
                    storage_json = page.evaluate(
                        "() => JSON.stringify(Object.assign({}, localStorage))"
                    )
                    storage = json.loads(storage_json)
                    for key, val in storage.items():
                        if isinstance(val, str) and len(val) > 50:
                            if val.startswith("eyJ") or "token" in key.lower() or "auth" in key.lower():
                                captured_token["value"] = val
                                break
                except Exception as e:
                    log.warning("localStorage read failed: {}".format(e))

        except Exception as e:
            log.error("Login failed: {}".format(e))
            browser.close()
            raise

        raw_cookies = context.cookies()
        browser.close()

    cookies = {c["name"]: c["value"] for c in raw_cookies}
    token   = captured_token["value"]

    session = {"cookies": cookies, "token": token}
    save_session(session)
    return session


# ==============================================================================
#  API HELPERS
# ==============================================================================

def _make_headers(token=None):
    headers = {
        "Content-Type":   "application/json",
        "Origin":         "https://atlas.urbanpiper.com",
        "Referer":        "https://atlas.urbanpiper.com/",
        "User-Agent":     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    if token:
        headers["Authorization"] = f"JWT {token}"
    return headers

GENERATE_REPORT_MUTATION = """
mutation generateReport($input: ReportGenerationInput!) {
  generateReportV2(input: $input) {
    status {
      success
      messages { field message __typename }
      __typename
    }
    __typename
  }
}
"""

GET_TASK_LIST_QUERY = """
query getReportsTaskList($limit: Int, $offset: Int, $filters: [ListFilterArgument]) {
  reportTaskList(limit: $limit, offset: $offset, filters: $filters) {
    count
    objects { status name jobId message downloadUrl __typename }
    __typename
  }
}
"""

REPORT_DOWNLOAD_BASE = "https://report.svc.urbanpiper.com/v1/report/{job_id}/download"

def _query_task_list(session):
    cookies = session.get("cookies", {})
    token   = session.get("token")
    headers = _make_headers(token)

    payload = {
        "operationName": "getReportsTaskList",
        "variables": {"limit": 100, "offset": 0, "filters": []},
        "query": GET_TASK_LIST_QUERY,
    }
    r = requests.post(config.ATLAS_GRAPHQL_URL, json=payload, headers=headers, cookies=cookies, timeout=30)
    r.raise_for_status()
    resp_json = r.json()
    if resp_json.get("data") is None:
        return []
    
    data = (resp_json.get("data") or {}).get("reportTaskList", {})
    count = data.get("count", 0)
    all_objects = data.get("objects", [])
    
    if count > 100:
        offset = (count // 100) * 100
        all_objects = []
        while True:
            payload["variables"]["offset"] = offset
            r = requests.post(config.ATLAS_GRAPHQL_URL, json=payload, headers=headers, cookies=cookies, timeout=30)
            r.raise_for_status()
            resp_json = r.json()
            if resp_json.get("data") is None:
                break
            data = (resp_json.get("data") or {}).get("reportTaskList", {})
            objects = data.get("objects", [])
            all_objects.extend(objects)
            if len(objects) < 100:
                break
            offset += 100
            
    return all_objects


def generate_and_download_csv(session, report_def, time_period="YESTERDAY", poll_interval=15, max_wait=1200):
    cookies = session.get("cookies", {})
    token   = session.get("token")
    headers = _make_headers(token)

    existing_ids = set()
    try:
        for t in _query_task_list(session):
            jid = t.get("jobId", "")
            if jid:
                existing_ids.add(jid)
    except Exception as e:
        pass

    notify_email = config.UP_EMAIL
    log.info(f"Queuing report '{report_def['name']}' ({report_def['id']}) | timePeriod={time_period}")

    payload = {
        "operationName": "generateReport",
        "variables": {
            "input": {
                "id":           report_def['id'],
                "name":         report_def['name'],
                "exportFormat": config.EXPORT_FORMAT,
                "emails":       [notify_email],
                "filters": {
                    "platforms":   config.PLATFORMS,
                    "locations":   config.LOCATION_IDS,
                    "orderStates": config.ORDER_STATES,
                    "timePeriod":  time_period,
                },
                "newVersion": True,
            }
        },
        "query": GENERATE_REPORT_MUTATION,
    }
    
    r = requests.post(config.ATLAS_GRAPHQL_URL, json=payload, headers=headers, cookies=cookies, timeout=30)
    r.raise_for_status()
    resp_json = r.json()

    if resp_json.get("data") is None:
        raise RuntimeError(f"generateReportV2 failed (data=null). errors={resp_json.get('errors')}")

    gql = (resp_json.get("data") or {}).get("generateReportV2", {}).get("status", {})
    if not gql.get("success"):
        raise RuntimeError(f"generateReportV2 failed: {gql.get('messages')}")

    log.info(f"Report queued. Polling every {poll_interval}s...")

    deadline  = time.time() + max_wait
    found_job = None

    while time.time() < deadline:
        time.sleep(poll_interval)
        tasks = _query_task_list(session)
        for task in tasks:
            jid = task.get("jobId", "")
            if jid in existing_ids:
                continue
            # UrbanPiper backend appends " csv" to the task name in the response!
            if task.get("name", "").startswith(report_def['name']):
                found_job = task
                break

        if not found_job:
            continue

        status = found_job.get("status", "")
        job_id = found_job.get("jobId", "")
        log.info(f"Status: {status} | jobId: {job_id}")

        if status == "completed":
            break
        elif status == "failed":
            raise RuntimeError(f"Atlas report FAILED: {found_job.get('message')}")

        found_job = None
    else:
        raise TimeoutError("Report did not complete in time.")

    job_id       = found_job.get("jobId", "")
    download_url = found_job.get("downloadUrl") or REPORT_DOWNLOAD_BASE.format(job_id=job_id)
    log.info(f"Downloading from: {download_url}")

    dl = requests.get(download_url, cookies=cookies,
                      headers={"User-Agent": headers["User-Agent"], "Authorization": headers.get("Authorization", "")},
                      timeout=120, stream=True)
    dl.raise_for_status()
    csv_bytes = dl.content
    log.info(f"Downloaded {len(csv_bytes):,} bytes.")
    return csv_bytes, job_id

def get_session_with_retry():
    session = load_cached_session()
    if session:
        return session
    return login_and_get_session()

def delete_and_relogin():
    delete_session()
    return login_and_get_session()
