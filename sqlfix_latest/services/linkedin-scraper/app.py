"""
Low-memory LinkedIn scraper microservice (drissbri-compatible endpoints).

Endpoints:
  GET  /health
  GET  /company-data/{linkedin_id}
  GET  /profile-data/{linkedin_id}
  POST /company-search  {name, location}

Auth: X-Li-At or Authorization: Bearer <li_at cookie>

Memory strategy:
  - One shared headless Chromium (reuse across requests)
  - No images, small window, disable GPU/extensions
  - Concurrency lock (1 scrape at a time)
  - Optional --single-process via env CHROME_SINGLE_PROCESS=1
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("linkedin-scraper")

app = FastAPI(title="linkedin-scraper", version="0.2.0-lowmem")

CHROME_BIN = os.getenv("CHROME_BIN", "/usr/bin/chromium")
DRIVER_PATH = os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver")
PAGE_WAIT = float(os.getenv("LINKEDIN_PAGE_WAIT", "2.5"))
SINGLE_PROCESS = os.getenv("CHROME_SINGLE_PROCESS", "0").lower() in ("1", "true", "yes")

_lock = threading.Lock()
_driver: Optional[webdriver.Chrome] = None
_driver_cookie: Optional[str] = None


def _chrome_options(single_process: bool | None = None) -> Options:
    use_sp = SINGLE_PROCESS if single_process is None else single_process
    opts = Options()
    opts.binary_location = CHROME_BIN
    opts.page_load_strategy = "eager"
    for arg in (
        "--headless=new",
        "--no-sandbox",
        "--disable-software-rasterizer",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-default-apps",
        "--disable-sync",
        "--disable-translate",
        "--hide-scrollbars",
        "--metrics-recording-only",
        "--mute-audio",
        "--no-first-run",
        "--safebrowsing-disable-auto-update",
        "--window-size=800,600",
        "--blink-settings=imagesEnabled=false",
        "--js-flags=--max-old-space-size=192",
        "user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    ):
        opts.add_argument(arg)
    # single-process often causes exit code 5 on Codespaces — optional only
    if use_sp:
        opts.add_argument("--single-process")
    opts.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2,
        "profile.default_content_setting_values.notifications": 2,
    })
    return opts


def _create_driver(li_at: str) -> webdriver.Chrome:
    service = Service(DRIVER_PATH)
    last_err: Exception | None = None
    for use_sp in (False, True):
        try:
            logger.info("starting chromium single_process=%s", use_sp)
            driver = webdriver.Chrome(
                service=service, options=_chrome_options(single_process=use_sp)
            )
            driver.set_page_load_timeout(45)
            driver.get("https://www.linkedin.com")
            driver.add_cookie({
                "name": "li_at",
                "value": li_at,
                "domain": ".linkedin.com",
                "path": "/",
                "secure": True,
                "httpOnly": True,
            })
            return driver
        except Exception as e:
            last_err = e
            logger.warning("Chrome start failed (single_process=%s): %s", use_sp, e)
    raise RuntimeError(f"Chrome failed to start: {last_err}")


def _get_driver(li_at: str) -> webdriver.Chrome:
    """Reuse one browser; recreate if cookie changed or session died."""
    global _driver, _driver_cookie
    if _driver is not None and _driver_cookie == li_at:
        try:
            _ = _driver.current_url
            return _driver
        except Exception:
            logger.warning("stale browser — recreating")
            try:
                _driver.quit()
            except Exception:
                pass
            _driver = None
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None
    logger.info("starting low-mem chromium")
    _driver = _create_driver(li_at)
    _driver_cookie = li_at
    return _driver


def _auth_cookie(
    authorization: Optional[str] = None,
    x_li_at: Optional[str] = None,
) -> str:
    if x_li_at:
        return x_li_at.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    raise HTTPException(401, "Missing X-Li-At or Authorization Bearer cookie")


def _check_authwall(driver: webdriver.Chrome) -> None:
    url = (driver.current_url or "").lower()
    if "login" in url or "authwall" in url or "checkpoint" in url:
        raise HTTPException(401, "linkedin_cookie_expired")


@app.on_event("shutdown")
def _shutdown():
    global _driver
    if _driver is not None:
        try:
            _driver.quit()
        except Exception:
            pass
        _driver = None


@app.get("/health")
def health():
    return {
        "ok": True,
        "browser_alive": _driver is not None,
        "single_process": SINGLE_PROCESS,
        "version": "0.2.0-lowmem",
    }


@app.get("/company-data/{linkedin_id}")
def company_data(
    linkedin_id: str,
    authorization: Optional[str] = Header(None),
    x_li_at: Optional[str] = Header(None, alias="X-Li-At"),
):
    li_at = _auth_cookie(authorization, x_li_at)
    url = f"https://www.linkedin.com/company/{linkedin_id.rstrip('/')}/"
    with _lock:
        driver = _get_driver(li_at)
        try:
            driver.get(url)
            time.sleep(PAGE_WAIT)
            _check_authwall(driver)
            title = driver.title or ""
            name = title.split("|")[0].strip() if title else linkedin_id
            employee_range = None
            try:
                for e in driver.find_elements(
                    By.CSS_SELECTOR, "div.org-top-card-summary-info-list__info-item"
                ):
                    t = e.text or ""
                    if "employee" in t.lower():
                        employee_range = t
                        break
            except Exception:
                pass
            return {
                "url": url,
                "linkedin_url": url,
                "name": name,
                "employee_range": employee_range,
                "employees": [],
                "people": [],
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.exception("company-data failed")
            raise HTTPException(500, str(e))


@app.get("/profile-data/{linkedin_id}")
def profile_data(
    linkedin_id: str,
    authorization: Optional[str] = Header(None),
    x_li_at: Optional[str] = Header(None, alias="X-Li-At"),
):
    li_at = _auth_cookie(authorization, x_li_at)
    url = f"https://www.linkedin.com/in/{linkedin_id.rstrip('/')}/"
    with _lock:
        driver = _get_driver(li_at)
        try:
            driver.get(url)
            time.sleep(PAGE_WAIT)
            _check_authwall(driver)
            title = driver.title or ""
            return {
                "url": url,
                "linkedin_url": url,
                "name": title.split("-")[0].strip() if title else linkedin_id,
                "headline": title,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e))


class CompanySearch(BaseModel):
    name: str
    location: str = ""


@app.post("/company-search")
def company_search(
    body: CompanySearch,
    authorization: Optional[str] = Header(None),
    x_li_at: Optional[str] = Header(None, alias="X-Li-At"),
):
    li_at = _auth_cookie(authorization, x_li_at)
    q = body.name
    if body.location:
        q = f"{body.name} {body.location}"
    from urllib.parse import quote_plus
    search_url = f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(q)}"
    with _lock:
        driver = _get_driver(li_at)
        try:
            driver.get(search_url)
            time.sleep(PAGE_WAIT)
            _check_authwall(driver)
            links = driver.find_elements(By.CSS_SELECTOR, "a[href*='/company/']")
            for a in links:
                href = a.get_attribute("href") or ""
                if "/company/" not in href:
                    continue
                slug = href.split("/company/")[1].split("/")[0].split("?")[0]
                if not slug or slug in ("undefined",):
                    continue
                return {
                    "url": f"https://www.linkedin.com/company/{slug}/",
                    "linkedin_url": f"https://www.linkedin.com/company/{slug}/",
                    "name": body.name,
                    "employee_range": None,
                    "employees": [],
                }
            return {}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e))
