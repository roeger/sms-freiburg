import logging
import re
import time

from selenium import webdriver
from selene import browser, be
from selenium.common.exceptions import (NoSuchElementException,
                                        StaleElementReferenceException)

URL = "https://sms-freiburg.de"

logger = logging.getLogger(__name__)


class SMSFreiburgClient:
    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    def _clean_description(self, text: str) -> str:
        # remove allergy indicators
        text = re.sub(r"\b(?:[A-Z]\d*|\d+)(?:,\s*(?:[A-Z]\d*|\d+))*\b,?", "",
                      text,)
        # use a semicolon to separate different parts of the meal
        text = re.sub(r"(?:\r?\n){3}", "; ", text)
        # Normalize whitespaces
        return " ".join(text.split())

    def _login(self, username: str, password: str):
        logger.debug(f"Logging in as {username}")
        browser.element("#ID_USERNAME").should(be.visible).type(username)
        browser.element("#ID_PASSWORD").should(be.visible).type(password)
        browser.element("#ID_LOGIN").should(be.visible).click()

    def _extract_meal_plan(self) -> dict[str, list]:
        result = {}

        # The relevant table is the one containing the day headers.
        table = browser.element("table.Table_Standard")

        # Get the two rows of the outer table.
        rows = table.locate().find_elements("xpath", "./tbody/tr")
        if len(rows) < 2:
            logger.debug("No meal rows found, skipping week")
            return result # no meals in this week (can happen during vacations)

        header_cells = rows[0].find_elements("xpath", "./td")
        food_cells = rows[1].find_elements("xpath", "./td")

        # First column is the menu type and price.
        # The remaining columns correspond to the dates.
        for header_cell, food_cell in zip(header_cells[1:], food_cells[1:]):
            header_text = header_cell.text

            # Extract date from header text
            match = re.search(r"\b(\d{2}\.\d{2}\.\d{2})\b", header_text)
            if not match:
                logger.debug("No date in header cell: %r", header_text)
                continue
            date = match.group(1)

            # The food cell contains a nested table.
            nested_rows = food_cell.find_elements(
                "xpath", ".//table/tbody/tr"
            )

            if not nested_rows:
                logger.debug("No meal entry for %s", date)
                continue

            # First row of nested table contains the food description.
            description_cell = nested_rows[0].find_element("xpath", "./td")
            description = self._clean_description(description_cell.text)

            # Second row contains "Bestellt: N".
            ordered = 0
            if len(nested_rows) > 1:
                ordered_text = nested_rows[1].text

                match = re.search(r"Bestellt:\s*(\d+)", ordered_text)
                if match:
                    ordered = int(match.group(1))
                else:
                    logger.warning("Could not parse order count for %s: %r",
                                   date, ordered_text)
            result[date] = [ordered, description]
            logger.debug("Parsed %s: ordered=%d, description=%r",
                         date, ordered, description)
        return result

    def _get_displayed_dates(self):
        table = browser.element("table.Table_Standard").locate()
        header_cells = table.find_elements("xpath", "./tbody/tr[1]/td")
        dates = [cell.text for cell in header_cells]
        return dates

    def _go_to_next_week(self) -> bool:
        old_dates = self._get_displayed_dates()

        next_button = browser.all(
            'a:has(img[alt="Eine Woche vor"])'
        )
        if len(next_button) == 0:
            logger.info("No 'next week' button; reached the last week")
            return False
        logger.debug("Navigating to next week")
        next_button.first.click()

        def new_week_is_ready(_):
            try:
                new_dates = self._get_displayed_dates()

                if not new_dates or new_dates == old_dates:
                    return False
                return True
            except Exception:
                # The portal may temporarily have detached elements while
                # replacing the table. Keep polling.
                return False

        browser.element("table.Table_Standard").should(new_week_is_ready)
        return True

    def _extract_meal_plan_for_week(self, max_attempts=10):
        """
        Retry the complete extraction if the portal replaces
        the table while we are reading it.
        """

        for attempt in range(max_attempts):
            try:
                return self._extract_meal_plan()

            except (StaleElementReferenceException, NoSuchElementException):
                if attempt == max_attempts - 1:
                    logger.error("Meal plan extraction failed after %d "
                                 "attempts", max_attempts)
                    raise

            time.sleep(0.1)
        raise RuntimeError("Could not extract meal plan")

    def get_data(self):
        logger.debug("Starting meal plan scrape from %s", URL)
        options = webdriver.ChromeOptions()
        options.add_argument("--headless=new")
        browser.config.driver_options = options
        browser.config.window_width = 1920
        browser.config.window_height = 1080
        browser.open(URL)

        self._login(self.username, self.password)
        browser.element('a[title="Speiseplan"]').should(be.visible).click()
        browser.element("table.Table_Standard").should(be.present)

        all_meals = {}

        while True:
            all_meals.update(self._extract_meal_plan_for_week())
            if not self._go_to_next_week():
                break

        logger.info("Scrape finished.")
        return all_meals
