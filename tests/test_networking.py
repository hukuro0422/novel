import unittest
from unittest.mock import Mock

from networking import AccessRestrictedError, WorkNotFoundError, validate_response


def response(status_code, retry_after=None):
    result = Mock()
    result.status_code = status_code
    result.headers = {}
    if retry_after is not None:
        result.headers["Retry-After"] = retry_after
    result.raise_for_status.return_value = None
    return result


class ValidateResponseTests(unittest.TestCase):
    def test_404_is_not_treated_as_zero_chapters(self):
        with self.assertRaises(WorkNotFoundError):
            validate_response(response(404), "https://example.test/work")

    def test_403_stops_processing(self):
        with self.assertRaises(AccessRestrictedError):
            validate_response(response(403), "https://example.test/work")

    def test_429_reports_retry_after(self):
        with self.assertRaisesRegex(AccessRestrictedError, "120秒"):
            validate_response(response(429, "120"), "https://example.test/work")

    def test_success_is_accepted(self):
        result = response(200)
        validate_response(result, "https://example.test/work")
        result.raise_for_status.assert_called_once_with()


class NetworkingConfigTests(unittest.TestCase):
    def test_interval_defaults(self):
        from networking import get_networking_config, PoliteSession
        config = get_networking_config()
        self.assertLessEqual(config["minimum_interval"], 0.5)
        self.assertLessEqual(config["narou_minimum_interval"], 1.0)
        session = PoliteSession()
        self.assertLessEqual(session.minimum_interval, 0.5)
        self.assertLessEqual(session.narou_minimum_interval, 1.0)
