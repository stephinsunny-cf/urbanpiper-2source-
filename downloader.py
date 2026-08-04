import sys
import json
import os
import time
import requests
import logging

def _get_log_path():
    """Write logs next to the .exe when frozen, or next to the script when developing."""
    if getattr(sys, 'frozen', False):
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "report_log.txt")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(_get_log_path(), encoding="utf-8"),
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

def get_base_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def load_cached_session():
    path = os.path.join(get_base_dir(), config.SESSION_FILE)
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
    path = os.path.join(get_base_dir(), config.SESSION_FILE)
    with open(path, "w") as f:
        json.dump(session, f)
    log.info("Session saved (token present: {}).".format(bool(session.get("token"))))


def delete_session():
    path = os.path.join(get_base_dir(), config.SESSION_FILE)
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
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--window-size=1280,800",
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="Asia/Kolkata",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            java_script_enabled=True,
            ignore_https_errors=True,
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        # Hide webdriver property to avoid bot detection
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        """)

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
            # Use 'load' instead of 'commit' so Cloudflare challenge fully resolves
            page.goto(config.ATLAS_LOGIN_URL, wait_until="load", timeout=60000)
            # Extra wait for any JS-based redirects/challenges to settle
            page.wait_for_timeout(3000)
            log.info("Initial URL: {}".format(page.url))

            email_sel = (
                "input[type=email]:visible, input[type=text]:visible, input[name=email]:visible, "
                "input[name=username]:visible, input[name=email_mobile]:visible, "
                "input[placeholder*=mail i]:visible, input[placeholder*=mobile i]:visible, "
                "input[placeholder*=email i]:visible"
            )
            page.wait_for_selector(email_sel, state="visible", timeout=60000)
            # Slow human-like typing instead of instant fill
            page.locator(email_sel).first.click()
            page.keyboard.type(config.UP_EMAIL, delay=80)
            page.locator("button[type=submit], input[type=submit]").first.click()

            page.wait_for_selector("input[type=password]", state="visible", timeout=30000)
            page.locator("input[type=password]").first.click()
            page.keyboard.type(config.UP_PASSWORD, delay=80)
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

    business = os.environ.get("BUSINESS_NAME", "Eatfit - MOC").strip().lower()
    location_ids = [] if ("cakezone" in business or "cake zone" in business) else config.LOCATION_IDS

    payload = {
        "operationName": "generateReport",
        "variables": {
            "input": {
                "id":           report_def['id'],
                "name":         report_def['name'],
                "exportFormat": config.EXPORT_FORMAT,
                "emails":       [notify_email],
                "filters": {
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

    if resp_json.get("data") is None and resp_json.get("errors"):
        raise RuntimeError(f"GraphQL Error: {resp_json.get('errors')}")

    data_block = resp_json.get("data") or {}
    gen_report = data_block.get("generateReportV2")
    if gen_report is None:
        raise RuntimeError(f"generateReportV2 returned null. Response: {resp_json}")

    gql = gen_report.get("status", {})
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
