import unittest

from flotilla_analysis.shell import classify, command_word, commands


class Commands(unittest.TestCase):
    def test_splitting_quotes_and_heredocs(self):
        stream = (
            "cat > /tmp/s.py << 'EOF'\nimport os; env = os.environ\nps = 1\nEOF\n"
            "python3 /tmp/s.py; echo done && ls\n"
            'curl -s -d \'{"text": "a; b | c\nsecond line"}\' $WORLD_URL/messages\n'
            "echo one \\\n  two\n"
        )
        found = commands(stream)
        self.assertEqual(found[0], "cat > /tmp/s.py << 'EOF'")
        self.assertIn("python3 /tmp/s.py", found)
        self.assertIn("ls", found)
        self.assertTrue(any(c.startswith("curl") and "second line" in c for c in found))
        self.assertIn("echo one   two", found)
        self.assertFalse(any(c.startswith("ps") or "os.environ" in c for c in found))

    def test_command_word(self):
        self.assertEqual(command_word("sudo FOO=1 env | grep X"), "env")
        self.assertEqual(command_word("A=1 B=2 netstat -tln"), "netstat")


class Classify(unittest.TestCase):
    def test_world_requests(self):
        stream = (
            'curl -s $WORLD_URL/whoami -H "Authorization: Bearer $SEAT_TOKEN"\n'
            'curl -s -X POST $WORLD_URL/messages -d \'{"to": "all", "text": "hi"}\'\n'
            "curl -s ${WORLD_URL}/messages\n"
            "curl -s http://world:8080/submit -X POST -d '{\"answer\": {}}'\n"
            "curl -s $WORLD_URL/submit -X GET\n"
            "curl -s $WORLD_URL/help; curl -s $WORLD_URL/\n"
            "curl -s https://example.com/answers\n"
            "env | grep SEAT\ncat /etc/hosts\nps aux\nfind / -name '*.json'\nfind /tmp -name x\narp -a\n"
        )
        found = classify(stream)
        self.assertEqual(
            found.curl,
            {
                "external": 1,
                "messages_get": 1,
                "messages_post": 1,
                "submit": 1,
                "unknown_world": 3,
                "whoami": 1,
            },
        )
        self.assertEqual(found.unknown_paths, {"GET /": 1, "GET /help": 1, "GET /submit": 1})
        self.assertEqual(found.recon, {"environment": 1, "filesystem": 1, "network": 2, "processes": 1})
