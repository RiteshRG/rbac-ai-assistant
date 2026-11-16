from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest


class ChatRenderingTests(unittest.TestCase):
    def test_assistant_history_escapes_dollars_before_markdown_rendering(self) -> None:
        class Client:
            is_mock = False

        app_path = Path(__file__).parents[1] / "app.py"
        app = AppTest.from_file(str(app_path))
        app.session_state["auth_token"] = "test-token"
        app.session_state["backend_client"] = Client()
        app.session_state["chat_list_loaded"] = True
        app.session_state["chats"] = []
        app.session_state["messages"] = [
            {
                "type": "assistant",
                "status": "ok",
                "text": "The amount is $75,000.",
                "citations": [],
            }
        ]

        app.run().switch_page("pages/chat.py").run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any(r"\$75,000" in element.value for element in app.markdown)
        )


if __name__ == "__main__":
    unittest.main()
