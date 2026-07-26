import sys
import logging
from datetime import date, timedelta
import config
import downloader
import email_sender

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

def main():
    is_test = False
    if len(sys.argv) > 1 and sys.argv[1].lower() == "test":
        is_test = True
        log.info("Running in TEST mode")

    if not config.UP_EMAIL or config.UP_EMAIL == "your-email@company.com":
        log.error("UP_EMAIL not set!")
        sys.exit(1)
    if not config.UP_PASSWORD or config.UP_PASSWORD == "your-password-here":
        log.error("UP_PASSWORD not set!")
        sys.exit(1)

    today = date.today()
    # The user requested yesterday's date to be downloaded always. 
    # UrbanPiper's API uses "YESTERDAY" for the timePeriod
    date_str = (today - timedelta(days=1)).strftime("%d %b %Y")
    
    log.info("=" * 60)
    log.info(f"UrbanPiper 2-Source Downloader")
    log.info(f"Date: {date_str} (Downloading YESTERDAY's data)")
    log.info("=" * 60)

    session = downloader.get_session_with_retry()

    reports_data = []

    for report_def in config.REPORTS:
        log.info(f"--- Fetching Report: {report_def['name']} ---")
        try:
            csv_bytes, job_id = downloader.generate_and_download_csv(session, report_def, time_period="YESTERDAY")
            filename = f"{report_def['name'].replace(' ', '_')}_{date_str.replace(' ', '_')}.csv"
            reports_data.append((filename, csv_bytes))
        except Exception as e:
            log.error(f"Failed to fetch {report_def['name']}: {e}")
            log.info("Attempting re-login and retry...")
            try:
                session = downloader.delete_and_relogin()
                csv_bytes, job_id = downloader.generate_and_download_csv(session, report_def, time_period="YESTERDAY")
                filename = f"{report_def['name'].replace(' ', '_')}_{date_str.replace(' ', '_')}.csv"
                reports_data.append((filename, csv_bytes))
            except Exception as retry_e:
                log.error(f"Failed to fetch {report_def['name']} after retry: {retry_e}")

    if not reports_data:
        log.error("No reports were successfully downloaded. Exiting.")
        sys.exit(1)

    log.info("=" * 60)
    log.info("Sending Email...")
    try:
        email_sender.send_reports_email(reports_data, date_str, is_test=is_test)
    except Exception as e:
        log.error(f"Failed to send email: {e}")

    log.info("=" * 60)
    log.info("All done.")

if __name__ == "__main__":
    main()
