from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, Log, Static

from .graph import ask


class RagDashboard(App):
    CSS = """
    Screen {
        background: #10151f;
        color: #e6edf3;
    }
    #left, #right {
        border: round #2b3445;
        padding: 1;
    }
    #status {
        height: 3;
    }
    #output {
        height: 1fr;
    }
    Input {
        margin-top: 1;
    }
    """

    BINDINGS = [("q", "quit", "Quit")]

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="left"):
                yield Static("Local RAG Dashboard", id="status")
                yield Input(placeholder="Ask a question and press Enter")
            with Vertical(id="right"):
                yield Log(id="output")
        yield Footer()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query:
            return
        log = self.query_one("#output", Log)
        status = self.query_one("#status", Static)
        status.update(f"Routing: {query}")
        log.write(f"> {query}")
        result = await ask(query)
        answer = result.get("final_answer") or result.get("general_answer") or "No answer produced."
        log.write(answer)
        status.update("Idle")
        event.input.value = ""


def main() -> None:
    RagDashboard().run()


if __name__ == "__main__":
    main()

