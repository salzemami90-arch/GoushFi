import os
import unittest
from unittest.mock import patch

from services.supabase_sync import SupabaseSyncClient


class SupabaseSyncClientConfigTests(unittest.TestCase):
    def test_from_runtime_reads_url_key_and_table_from_secrets(self):
        client = SupabaseSyncClient.from_runtime(
            {
                "SUPABASE_URL": "https://example.supabase.co",
                "SUPABASE_ANON_KEY": "anon-key",
                "SUPABASE_DATA_TABLE": "custom_table",
            }
        )

        self.assertEqual(client.supabase_url, "https://example.supabase.co")
        self.assertEqual(client.anon_key, "anon-key")
        self.assertEqual(client.table_name, "custom_table")

    def test_from_runtime_reads_nested_supabase_section(self):
        client = SupabaseSyncClient.from_runtime(
            {
                "supabase": {
                    "url": "https://nested.supabase.co",
                    "anon_key": "nested-anon-key",
                    "table_name": "nested_table",
                }
            }
        )

        self.assertEqual(client.supabase_url, "https://nested.supabase.co")
        self.assertEqual(client.anon_key, "nested-anon-key")
        self.assertEqual(client.table_name, "nested_table")

    def test_from_runtime_reads_connections_supabase_section(self):
        client = SupabaseSyncClient.from_runtime(
            {
                "connections": {
                    "supabase": {
                        "url": "https://connection.supabase.co",
                        "api_key": "connection-anon-key",
                        "data_table": "connection_table",
                    }
                }
            }
        )

        self.assertEqual(client.supabase_url, "https://connection.supabase.co")
        self.assertEqual(client.anon_key, "connection-anon-key")
        self.assertEqual(client.table_name, "connection_table")

    def test_from_runtime_falls_back_to_environment(self):
        with patch.dict(
            os.environ,
            {
                "SUPABASE_URL": "https://env.supabase.co",
                "SUPABASE_ANON_KEY": "env-key",
                "SUPABASE_DATA_TABLE": "env_table",
            },
            clear=False,
        ):
            client = SupabaseSyncClient.from_runtime()

        self.assertEqual(client.supabase_url, "https://env.supabase.co")
        self.assertEqual(client.anon_key, "env-key")
        self.assertEqual(client.table_name, "env_table")


if __name__ == "__main__":
    unittest.main()
