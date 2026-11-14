"""수집 차단과 HTTP 재시도 한도를 네트워크 없이 검증한다."""
import unittest
from unittest.mock import Mock, patch

import requests

import collect_korea
from common import Client, CollectionStopped


class CollectionRulesTest(unittest.TestCase):
    def test_robots_disallow_stops_before_any_api(self):
        client = Mock()
        client.get.return_value.text = 'User-agent: *\nDisallow: /\n'
        with patch.object(collect_korea, 'Client', return_value=client), patch.object(collect_korea, 'save_json') as save:
            with self.assertRaises(CollectionStopped):
                collect_korea.collect()
        client.get.assert_called_once_with('https://librsv.korea.ac.kr/robots.txt')
        self.assertEqual(save.call_args.args[1]['status'], 'blocked_by_robots')

    def test_literal_escaped_newlines_also_block(self):
        client = Mock()
        client.get.return_value.text = r'User-agent: *\r\nDisallow: /'
        with patch.object(collect_korea, 'Client', return_value=client), patch.object(collect_korea, 'save_json'):
            with self.assertRaises(CollectionStopped):
                collect_korea.collect()
        self.assertEqual(client.get.call_count, 1)

    def test_network_failure_retries_at_most_twice(self):
        client = Client()
        client.session.get = Mock(side_effect=requests.Timeout())
        with patch('common.time.sleep') as sleep:
            with self.assertRaises(CollectionStopped):
                client.get('https://example.org/known-endpoint')
        self.assertEqual(client.session.get.call_count, 3)
        self.assertEqual(sleep.call_count, 3)
        self.assertTrue(all(call.args[0] >= 1.0 for call in sleep.call_args_list))

    def test_login_redirect_stops_without_retry(self):
        client = Client()
        client.session.get = Mock(return_value=Mock(is_redirect=True))
        with patch('common.time.sleep'):
            with self.assertRaises(CollectionStopped):
                client.get('https://example.org/known-endpoint')
        self.assertEqual(client.session.get.call_count, 1)


if __name__ == '__main__':
    unittest.main()
