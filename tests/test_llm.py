"""Optional model client tests using only simulated data and a loopback server."""
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from services.common.llm import ModelAnswerError, complete_json, is_configured


HAS_PACKAGES = all(importlib.util.find_spec(name) for name in ("openai", "jsonschema"))
MODEL_ENV = ("MODEL_BASE_URL", "MODEL_NAME", "MODEL_TOKEN_SCOPE", "MODEL_API_KEY")


class Configuration(unittest.TestCase):
    def test_not_configured(self):
        with patch.dict(os.environ, {key: "" for key in MODEL_ENV}):
            self.assertFalse(is_configured())
            with self.assertRaisesRegex(ModelAnswerError, "MODEL_BASE_URL"):
                complete_json("Simulated data", "test", "does-not-exist.json")

    def test_import_needs_no_optional_packages(self):
        script = """
import sys
class BlockOptional:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('openai', 'azure', 'jsonschema'):
            raise AssertionError('optional import: ' + fullname)
sys.meta_path.insert(0, BlockOptional())
import services.common.llm
assert 'openai' not in sys.modules
assert 'jsonschema' not in sys.modules
assert 'azure.identity' not in sys.modules
"""
        result = subprocess.run(
            [sys.executable, "-c", script], capture_output=True, text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_model_name_required(self):
        with patch.dict(os.environ, {"MODEL_BASE_URL": "http://127.0.0.1/v1/", "MODEL_NAME": ""}):
            self.assertTrue(is_configured())
            with self.assertRaisesRegex(ModelAnswerError, "MODEL_NAME"):
                complete_json("", "", "does-not-exist.json")

    def test_api_key_rejected_for_remote_endpoints(self):
        for endpoint in (
            "https://example.openai.azure.com/openai/v1/",
            "https://localhost.example.com/v1/",
        ):
            with self.subTest(endpoint=endpoint), patch.dict(os.environ, {
                "MODEL_BASE_URL": endpoint, "MODEL_NAME": "test", "MODEL_API_KEY": "dummy",
            }):
                with self.assertRaisesRegex(ModelAnswerError, "loopback"):
                    complete_json("", "", "does-not-exist.json")


@unittest.skipUnless(HAS_PACKAGES, "optional openai/jsonschema packages are not installed")
class LocalModel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.schema = Path(self.tmp.name) / "answer.schema.json"
        self.schema.write_text(json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object", "properties": {"text": {"type": "string"}},
            "required": ["text"], "additionalProperties": False,
        }), encoding="utf-8")
        self.answers = []
        self.requests = []
        self.status = 200
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((self.path, body))
                content = owner.answers.pop(0)
                response = json.dumps({
                    "id": "simulated", "object": "chat.completion", "created": 0,
                    "model": body["model"],
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": content}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 6, "total_tokens": 18},
                }).encode("utf-8")
                self.send_response(owner.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join)
        self.addCleanup(self.server.shutdown)
        env = patch.dict(os.environ, {
            "MODEL_BASE_URL": f"http://127.0.0.1:{self.server.server_port}/v1/",
            "MODEL_NAME": "simulated-deployment", "MODEL_API_KEY": "dummy",
            "MODEL_TOKEN_SCOPE": "https://ai.azure.com/.default",
        })
        env.start()
        self.addCleanup(env.stop)

    def complete(self, images=None):
        return complete_json("Simulated data", "Simulated prompt", self.schema, images)

    def test_valid_answer_and_metadata_logging(self):
        self.answers = ['{"text":"Simulated data"}']
        with self.assertLogs("ghosttrace.llm", level="INFO") as logs:
            self.assertEqual(self.complete(), {"text": "Simulated data"})
        self.assertEqual(len(self.requests), 1)
        path, body = self.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(body["model"], "simulated-deployment")
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertIn(self.schema.read_text(encoding="utf-8"), body["messages"][0]["content"])
        self.assertNotIn("temperature", body)
        self.assertIn("deployment=simulated-deployment", logs.output[0])
        self.assertIn("latency_ms=", logs.output[0])
        self.assertIn("prompt_tokens=12 completion_tokens=6 total_tokens=18 status=ok", logs.output[0])
        self.assertNotIn("Simulated prompt", str(logs.output))
        self.assertNotIn("dummy", str(logs.output))

    def test_invalid_then_valid(self):
        for invalid in ("not JSON", '{"text":12}', "[]", '{"text":NaN}', None):
            with self.subTest(invalid=invalid):
                self.answers = [invalid, '{"text":"Simulated data"}']
                self.requests.clear()
                self.assertEqual(self.complete(), {"text": "Simulated data"})
                self.assertEqual(len(self.requests), 2)
                messages = self.requests[1][1]["messages"]
                if invalid:
                    self.assertEqual(messages[-2], {"role": "assistant", "content": invalid})
                self.assertEqual(messages[-1]["role"], "user")

    def test_invalid_twice(self):
        self.answers = ["not JSON", '{"text":12}']
        with self.assertLogs("ghosttrace.llm", level="INFO") as logs:
            with self.assertRaisesRegex(ModelAnswerError, "twice"):
                self.complete()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(sum("status=failed" in line for line in logs.output), 3)

    def test_images_from_bytes_and_path(self):
        image = b"\x89PNG\r\n\x1a\nsimulated-image"
        path = Path(self.tmp.name) / "simulated.png"
        path.write_bytes(image)
        self.answers = ['{"text":"Simulated data"}']
        with self.assertLogs("ghosttrace.llm", level="INFO") as logs:
            self.complete([image, path])
        parts = self.requests[0][1]["messages"][1]["content"]
        expected = "data:image/png;base64," + base64.b64encode(image).decode("ascii")
        self.assertEqual(parts[0], {"type": "text", "text": "Simulated prompt"})
        self.assertEqual([part["image_url"]["url"] for part in parts[1:]], [expected, expected])
        self.assertNotIn(expected, str(logs.output))

    def test_bad_image_does_not_call_model(self):
        with self.assertRaisesRegex(ModelAnswerError, "Images"):
            self.complete([b"not an image"])
        self.assertEqual(self.requests, [])

    def test_transport_failure_is_not_an_answer_retry(self):
        self.status = 500
        self.answers = ["Simulated server failure"]
        with self.assertLogs("ghosttrace.llm", level="INFO") as logs:
            with self.assertRaisesRegex(ModelAnswerError, "Model request failed"):
                self.complete()
        self.assertEqual(len(self.requests), 1)
        self.assertIn("status=failed", logs.output[0])

    def test_keyless_provider_and_scope(self):
        try:
            import azure.identity
        except ImportError:
            self.skipTest("optional azure-identity package is not installed")
        for scope in (None, "https://example.test/.default"):
            with self.subTest(scope=scope), patch.dict(os.environ, {"MODEL_API_KEY": ""}):
                if scope is None:
                    os.environ.pop("MODEL_TOKEN_SCOPE", None)
                else:
                    os.environ["MODEL_TOKEN_SCOPE"] = scope
                with patch("azure.identity.DefaultAzureCredential") as credential, patch(
                    "azure.identity.get_bearer_token_provider", return_value=lambda: "fake-token"
                ) as provider:
                    self.answers = ['{"text":"Simulated data"}']
                    self.assertEqual(self.complete(), {"text": "Simulated data"})
                    provider.assert_called_once_with(
                        credential.return_value, scope or "https://ai.azure.com/.default",
                    )
                    credential.return_value.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
