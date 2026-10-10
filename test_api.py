import importlib
import io
import sys
import unittest
from contextlib import redirect_stdout
from unittest import mock

import requests

KEY = "hemlig-testnyckel-123"
LEAKY_URL = f"https://api.the-odds-api.com/v4/sports?apiKey={KEY}"   # som requests egna felmeddelanden


def import_api():
    """Importerar api.py på nytt med load_dotenv avstängd, så .env aldrig läses."""
    sys.modules.pop("api", None)
    with mock.patch("dotenv.load_dotenv"):
        return importlib.import_module("api")


def response(data, status=200, headers=None):
    r = mock.Mock(status_code=status, headers=headers or {})
    r.json.return_value = data
    return r


def non_json_response(status):
    r = mock.Mock(status_code=status, headers={})
    r.json.side_effect = requests.exceptions.JSONDecodeError("Expecting value", "<html>", 0)
    return r


# Varje funktion i api.py med påhittade argument. Alla returnerar data först (get_pinnacle_odds som tuple).
CALLS = {
    "get_sports": lambda api: api.get_sports(),
    "get_odds": lambda api: api.get_odds("soccer_x", "eu", "h2h,totals"),
    "get_scores": lambda api: api.get_scores("soccer_x", 3, ["ev1", "ev2"]),
    "get_pinnacle_odds": lambda api: api.get_pinnacle_odds("soccer_x", "ev1", "h2h"),
}


def data_of(name, result):
    return result[0] if name == "get_pinnacle_odds" else result


class ApiTestCase(unittest.TestCase):
    """requests.get är alltid mockad, inga riktiga anrop görs."""

    @classmethod
    def setUpClass(cls):
        cls.api = import_api()

    @classmethod
    def tearDownClass(cls):
        sys.modules.pop("api", None)

    def setUp(self):
        patcher = mock.patch.object(self.api, "API_KEY", KEY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, name, **get_kwargs):
        """Anropar funktionen med mockad requests.get. Returnerar (resultat, utskrift, mock)."""
        with mock.patch.object(self.api.requests, "get", **get_kwargs) as get, \
                redirect_stdout(io.StringIO()) as out:
            result = CALLS[name](self.api)
        return result, out.getvalue(), get


class TestAllFunctions(ApiTestCase):
    def test_timeout_always_passed(self):
        self.assertEqual(self.api.REQUEST_TIMEOUT, (5, 30))
        for name in CALLS:
            with self.subTest(name):
                _, _, get = self.call(name, return_value=response([]))
                self.assertEqual(get.call_args.kwargs["timeout"], (5, 30))

    def test_normal_response(self):
        for name in CALLS:
            with self.subTest(name):
                result, _, _ = self.call(name, return_value=response([{"id": "ev1"}]))
                self.assertEqual(data_of(name, result), [{"id": "ev1"}])

    def test_network_errors_return_message_without_key(self):
        errors = [requests.exceptions.ReadTimeout(LEAKY_URL),
                  requests.exceptions.ConnectTimeout(LEAKY_URL),
                  requests.exceptions.ConnectionError(LEAKY_URL)]
        for name in CALLS:
            for error in errors:
                with self.subTest(name, error=type(error).__name__):
                    result, out, _ = self.call(name, side_effect=error)
                    data = data_of(name, result)
                    self.assertEqual(data, {"message": f"nätverksfel ({type(error).__name__})"})
                    self.assertTrue(self.api.is_network_error(data))
                    self.assertNotIn(KEY, out)

    def test_other_request_error_caught(self):
        for name in CALLS:
            with self.subTest(name):
                result, out, _ = self.call(name, side_effect=requests.exceptions.TooManyRedirects(LEAKY_URL))
                self.assertEqual(data_of(name, result), {"message": "anropsfel (TooManyRedirects)"})
                self.assertFalse(self.api.is_network_error(data_of(name, result)))
                self.assertNotIn(KEY, out)

    def test_http_error_with_api_message(self):
        for name in CALLS:
            with self.subTest(name):
                result, _, _ = self.call(name, return_value=response(
                    {"message": "Usage quota has been reached"}, status=429))
                self.assertEqual(data_of(name, result), {"message": "Usage quota has been reached"})
                self.assertFalse(self.api.is_network_error(data_of(name, result)))

    def test_http_error_without_json(self):
        for name in CALLS:
            with self.subTest(name):
                result, _, _ = self.call(name, return_value=non_json_response(502))
                self.assertEqual(data_of(name, result), {"message": "ogiltigt svar (HTTP 502)"})


class TestLastUsage(ApiTestCase):
    def test_set_from_headers_for_all_functions(self):
        headers = {"x-requests-last": "2", "x-requests-remaining": "498"}
        for name in CALLS:
            with self.subTest(name):
                self.call(name, return_value=response([], headers=headers))
                self.assertEqual(self.api.last_usage, (2, 498))

    def test_http_error_with_headers(self):
        headers = {"x-requests-last": "0", "x-requests-remaining": "0"}
        self.call("get_odds", return_value=response({"message": "Usage quota has been reached"},
                                                    status=429, headers=headers))
        self.assertEqual(self.api.last_usage, (0, 0))

    def test_reset_on_network_error(self):
        self.call("get_odds", return_value=response([], headers={"x-requests-last": "1",
                                                                 "x-requests-remaining": "9"}))
        self.call("get_odds", side_effect=requests.exceptions.ReadTimeout(LEAKY_URL))
        self.assertEqual(self.api.last_usage, (None, None))

    def test_missing_or_invalid_headers(self):
        self.call("get_sports", return_value=response([]))
        self.assertEqual(self.api.last_usage, (None, None))
        self.call("get_sports", return_value=response([], headers={"x-requests-last": "x",
                                                                   "x-requests-remaining": "12"}))
        self.assertEqual(self.api.last_usage, (None, 12))
        self.assertEqual(self.api.usage(None), (None, None))


class TestGetScores(ApiTestCase):
    def test_cost_printed(self):
        headers = {"x-requests-last": "2", "x-requests-remaining": "498"}
        _, out, get = self.call("get_scores", return_value=response([], headers=headers))
        self.assertIn("Scores soccer_x: kostnad 2, kvar 498", out)
        self.assertEqual(get.call_args.kwargs["params"],
                         {"apiKey": KEY, "daysFrom": 3, "eventIds": "ev1,ev2"})

    def test_nothing_printed_on_network_error(self):
        _, out, _ = self.call("get_scores", side_effect=requests.exceptions.ReadTimeout(LEAKY_URL))
        self.assertEqual(out, "")


class TestGetPinnacleOdds(ApiTestCase):
    def test_event_endpoint_pinnacle_only(self):
        headers = {"x-requests-last": "2", "x-requests-remaining": "498"}
        data = {"id": "ev1", "bookmakers": []}
        with mock.patch.object(self.api.requests, "get", return_value=response(data, headers=headers)) as get:
            result = self.api.get_pinnacle_odds("soccer_x", "ev1", "h2h,totals")
        url = get.call_args.args[0]
        params = get.call_args.kwargs["params"]
        self.assertTrue(url.endswith("/v4/sports/soccer_x/events/ev1/odds"))
        self.assertEqual(params, {"apiKey": KEY, "bookmakers": "pinnacle", "markets": "h2h,totals"})
        self.assertEqual(result, (data, "2", "498"))

    def test_network_error_no_cost(self):
        result, _, _ = self.call("get_pinnacle_odds", side_effect=requests.exceptions.ReadTimeout(LEAKY_URL))
        self.assertEqual(result[1:], (None, None))


if __name__ == "__main__":
    unittest.main()
