import json
import unittest

from examples.seatlab.visibility import omp_seat

_MODEL = "openai-codex/gpt-9.2-sol"
_TEXT = '  {"word":"café 𝄞"}\r\n\t '
_TURN = b'{"schema":"seatlab.seat-turn/v1","prompt":"hello"}\n'


def _frame(value):
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()


def _completion():
    return {
        "id": 1,
        "status": 200,
        "body": {
            "id": "provider-id-not-for-receipts",
            "model": _MODEL,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": _TEXT, "refusal": None},
                }
            ],
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 5,
                "total_tokens": 13,
                "prompt_tokens_details": {"cached_tokens": 3},
            },
        },
    }


class OmpSeatTest(unittest.TestCase):
    def test_request_rejects_bad_frames(self):
        cases = (
            b'{"schema":"seatlab.seat-turn/v1","prompt":"hello","prompt":"hello"}\n',
            b'{"schema":"seatlab.seat-turn/v1","prompt":""}\n',
            _TURN[:-1],
            _TURN + b"\n",
            _TURN + _TURN,
            _TURN.replace(b"hello", b"\xff"),
            b" " * (32 * 1024) + _TURN,
        )
        for frame in cases:
            with self.subTest(frame=frame[:80]), self.assertRaises(ValueError):
                omp_seat._parse_turn(frame)

    def test_model_requires_a_single_concrete_provider_id(self):
        omp_seat._validate_model(_MODEL)
        for model in (
            "gpt-9.2-sol",
            "openai-codex/auto",
            "openai-codex/gpt-9.2-latest",
            "openai-codex/openai/gpt-9.2-sol",
            "openai-codex/../gpt-9.2-sol",
            "https://openai-codex/gpt-9.2-sol",
        ):
            with self.subTest(model=model), self.assertRaises(ValueError):
                omp_seat._validate_model(model)

    def test_accepts_exact_reply_text_and_reported_usage(self):
        prompt = "  exact\n prompt 𝄞  "
        self.assertEqual(
            omp_seat._parse_turn(
                _frame(
                    {
                        "schema": "seatlab.seat-turn/v1",
                        "prompt": prompt,
                    }
                )
            ),
            prompt,
        )
        text, usage = omp_seat._parse_completion(_frame(_completion()), _MODEL)
        self.assertEqual(text, _TEXT)
        self.assertEqual(
            json.loads(omp_seat._encode_frame({"text": text})), {"text": _TEXT}
        )
        self.assertEqual(
            usage,
            {
                "prompt_tokens": 8,
                "completion_tokens": 5,
                "total_tokens": 13,
                "cached_tokens": 3,
            },
        )

    def test_rejects_gateway_failures(self):
        cases = (
            (("status",), 429),
            (("id",), True),
            (("body", "model"), "openai-codex/gpt-9.2-mini"),
            (("body", "choices"), _completion()["body"]["choices"] * 2),
            (("body", "choices", 0, "finish_reason"), "length"),
            (("body", "choices", 0, "message", "role"), "tool"),
            (("body", "choices", 0, "message", "refusal"), "refused"),
            (("body", "choices", 0, "message", "tool_calls"), [{"type": "function"}]),
            (("body", "choices", 0, "message", "function_call"), {"name": "f"}),
            (("body", "choices", 0, "message", "content"), "é" * 4097),
            (("body", "usage", "total_tokens"), 14),
            (("body", "usage", "prompt_tokens_details"), {}),
            (("body", "usage", "prompt_tokens_details", "cached_tokens"), -1),
        )
        for path, invalid in cases:
            response = _completion()
            target = response
            for key in path[:-1]:
                target = target[key]
            target[path[-1]] = invalid
            with self.subTest(path=path), self.assertRaises(ValueError):
                omp_seat._parse_completion(_frame(response), _MODEL)

    def test_rejects_duplicate_and_nonfinite_gateway_fields(self):
        frame = _frame(_completion())
        for replacement in (
            b'"cached_tokens":3,"cached_tokens":3',
            b'"cached_tokens":NaN',
            b'"cached_tokens":1e999',
        ):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                omp_seat._parse_completion(
                    frame.replace(b'"cached_tokens":3', replacement), _MODEL
                )
