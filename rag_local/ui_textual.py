from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, Container
from textual.widgets import Footer, Header, Input, Static, RichLog
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

from rag_local.config import SETTINGS
from rag_local.ingest import ingest_directory


class RagDashboard(App):
    """A premium, state-of-the-art terminal UI dashboard for local RAG execution."""

    CSS = """
    Screen {
        background: #0f141c;
        color: #e2e8f0;
    }
    #layout {
        layout: grid;
        grid-size: 2 1;
        grid-columns: 2fr 3fr;
        height: 1fr;
        margin-bottom: 1;
    }
    #left-col {
        padding: 0 1;
        height: 100%;
        layout: grid;
        grid-size: 1 3;
        grid-rows: 12 12 1fr;
        grid-gutter: 1;
    }
    #right-col {
        padding: 0 1;
        height: 100%;
        layout: grid;
        grid-size: 1 2;
        grid-rows: 3fr 2fr;
        grid-gutter: 1;
    }
    Input {
        background: #1e293b;
        border: tall #38bdf8;
        color: #ffffff;
        margin: 0 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("c", "clear_logs", "Clear Logs"),
        ("r", "re_ingest", "Re-Ingest Project"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.node_statuses: dict[str, str] = {}
        self.node_messages: dict[str, str] = {}
        self.worker_tasks: dict[str, dict[str, Any]] = {}
        self.sources: list[Any] = []
        self.answer_text: str = ""
        self.running: bool = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="layout"):
            with Vertical(id="left-col"):
                yield Static(self.get_pipeline_table(), id="pipeline-status")
                yield Static(self.get_workers_table(), id="workers-panel")
                yield Static(self.get_sources_panel(), id="sources-panel")
            with Vertical(id="right-col"):
                yield Static(Panel("", title="[bold cyan]Answer Box[/bold cyan]", border_style="blue"), id="answer-panel")
                yield RichLog(id="log-panel", max_lines=1000, wrap=True)
        yield Input(placeholder="Ask a question and press Enter...")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Local Router-Orchestrator RAG Dashboard"
        self.sub_title = f"Ollama Host: {SETTINGS.ollama_host} | Model: {SETTINGS.ollama_chat_model}"
        self.log_box = self.query_one("#log-panel", RichLog)
        self.answer_box = self.query_one("#answer-panel", Static)
        self.log_box.write("[System] Dashboard started. Ready for query.")

    def get_pipeline_table(self) -> Panel:
        table = Table(box=None, show_header=False, expand=True)
        table.add_column("Indicator", width=4)
        table.add_column("Node", width=22)
        table.add_column("Status", width=25)

        nodes = [
            ("router", "Router Agent", "Classifies query intent"),
            ("plan", "Orchestrator Planner", "Creates task execution plan"),
            ("retrieve", "Retriever Agent", "Retrieves context (Dense+BM25)"),
            ("workers", "Parallel Workers", "Runs tool tasks in parallel"),
            ("synthesize", "Synthesizer Node", "Generates final answer"),
        ]

        for code, label, desc in nodes:
            status = self.node_statuses.get(code, "pending")
            if status == "active":
                indicator = "[bold cyan]▶[/bold cyan]"
                node_str = f"[bold cyan]{label}[/bold cyan]"
                status_str = f"[bold cyan]{self.node_messages.get(code, 'Running...')}[/bold cyan]"
            elif status == "done":
                indicator = "[bold green]✓[/bold green]"
                node_str = f"[dim green]{label}[/dim green]"
                status_str = f"[dim green]{self.node_messages.get(code, 'Completed')}[/dim green]"
            elif status == "error":
                indicator = "[bold red]✗[/bold red]"
                node_str = f"[bold red]{label}[/bold red]"
                status_str = f"[bold red]{self.node_messages.get(code, 'Failed')}[/bold red]"
            else:
                indicator = "[dim white]·[/dim white]"
                node_str = f"[dim white]{label}[/dim white]"
                status_str = f"[dim white]{desc}[/dim white]"

            table.add_row(indicator, node_str, status_str)

        return Panel(table, title="[bold cyan]LangGraph Pipeline Steps[/bold cyan]", border_style="blue")

    def get_workers_table(self) -> Panel:
        table = Table(box=None, expand=True)
        table.add_column("Worker / Tool Task", width=22)
        table.add_column("Type", width=8)
        table.add_column("Status", width=14)

        if not self.worker_tasks:
            table.add_row("[dim]No tasks scheduled[/dim]", "-", "-")
        else:
            for name, w in self.worker_tasks.items():
                status = w["status"]
                kind = w["kind"]
                if status == "PENDING":
                    status_str = "[yellow]PENDING ⏱️[/yellow]"
                elif status == "RUNNING":
                    status_str = "[blue]RUNNING ⏳[/blue]"
                elif status == "SUCCESS":
                    status_str = "[green]SUCCESS ✓[/green]"
                elif status == "FAILED":
                    status_str = "[red]FAILED ✗[/red]"
                else:
                    status_str = status

                table.add_row(name, kind, status_str)

        return Panel(table, title="[bold cyan]Parallel Agents / Tools[/bold cyan]", border_style="blue")

    def get_sources_panel(self) -> Panel:
        table = Table(box=None, expand=True)
        table.add_column("Source Document Path", width=35)
        table.add_column("Score", width=8)

        if not self.sources:
            table.add_row("[dim]No source documents loaded[/dim]", "-")
        else:
            seen_files = set()
            for s in self.sources:
                path = s.get("source_path") if isinstance(s, dict) else getattr(s, "source_path", str(s))
                score = s.get("score") if isinstance(s, dict) else getattr(s, "score", 0.0)
                try:
                    rel_path = str(Path(path).relative_to(Path(".").resolve()))
                except Exception:
                    rel_path = Path(path).name
                
                if rel_path not in seen_files:
                    seen_files.add(rel_path)
                    table.add_row(rel_path, f"{score:.2f}")

        return Panel(table, title="[bold cyan]Retrieved Documents[/bold cyan]", border_style="blue")

    def update_status(self, node: str, message: str, status: str = "active") -> None:
        self.node_statuses[node] = status
        self.node_messages[node] = message
        self.query_one("#pipeline-status", Static).update(self.get_pipeline_table())

    def update_workers(self, reset: bool = False) -> None:
        if reset:
            self.worker_tasks = {}
        self.query_one("#workers-panel", Static).update(self.get_workers_table())

    def update_sources(self, sources: list[Any]) -> None:
        self.sources = sources
        self.query_one("#sources-panel", Static).update(self.get_sources_panel())

    def on_token(self, token: str) -> None:
        self.answer_text += token
        self.answer_box.update(Panel(Markdown(self.answer_text), title="[bold cyan]Answer Box[/bold cyan]", border_style="blue"))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if not query or self.running:
            return
        
        self.running = True
        self.query_one(Input).value = ""
        self.answer_text = ""
        self.answer_box.update(Panel("", title="[bold cyan]Answer Box[/bold cyan]", border_style="blue"))
        self.log_box.write(f"\n>>> Query: {query}")
        
        self.node_statuses = {}
        self.node_messages = {}
        self.update_status(node="router", message="Analyzing intent...")
        self.update_workers(reset=True)
        self.update_sources([])

        config = {
            "configurable": {
                "token_callback": self.on_token
            }
        }
        
        from rag_local.graph import APP
        try:
            async for update in APP.astream({"user_input": query}, config=config, stream_mode="updates"):
                if "router" in update:
                    data = update["router"]
                    route = data.get("route", "unknown")
                    reason = data.get("route_reason", "")
                    self.log_box.write(f"[Router] Decision: route={route} reason='{reason}'")
                    self.update_status(node="router", message=f"Route: {route}", status="done")
                    if route == "general":
                        self.update_status(node="synthesize", message="Generating...", status="active")
                    else:
                        self.update_status(node="plan", message="Creating plan...", status="active")
                        
                elif "plan" in update:
                    data = update["plan"]
                    plan = data.get("plan")
                    if plan:
                        self.log_box.write(f"[Orchestrator] Objective: '{plan.objective}'")
                        self.log_box.write(f"[Orchestrator] Plan: {len(plan.tasks)} parallel tasks scheduled")
                        self.update_status(node="plan", message=f"Scheduled {len(plan.tasks)} tasks", status="done")
                        for task in plan.tasks:
                            self.worker_tasks[task.name] = {"kind": task.kind, "status": "PENDING"}
                        self.update_workers()
                        self.update_status(node="retrieve", message="Searching...", status="active")
                    
                elif "retrieve" in update:
                    data = update["retrieve"]
                    chunks = data.get("retrieved_chunks", [])
                    self.log_box.write(f"[Retriever] Retrieved {len(chunks)} relevant chunks")
                    self.update_status(node="retrieve", message=f"Found {len(chunks)} chunks", status="done")
                    self.update_sources(chunks)
                    if self.worker_tasks:
                        self.update_status(node="workers", message="Running workers...", status="active")
                        for k in self.worker_tasks:
                            self.worker_tasks[k]["status"] = "RUNNING"
                        self.update_workers()
                    else:
                        self.update_status(node="synthesize", message="Generating...", status="active")

                elif "workers" in update:
                    data = update["workers"]
                    code_results = data.get("code_results", [])
                    web_results = data.get("web_results", [])
                    self.log_box.write("[Orchestrator] Parallel workers completed tasks")
                    for res in code_results + web_results:
                        status_str = "SUCCESS" if res.success else "FAILED"
                        if res.task_name in self.worker_tasks:
                            self.worker_tasks[res.task_name]["status"] = status_str
                        self.log_box.write(f"  - Worker [{res.task_name}]: {res.summary}")
                    self.update_workers()
                    self.update_status(node="workers", message="Done", status="done")
                    self.update_status(node="synthesize", message="Generating...", status="active")
                    
                elif "synthesize" in update:
                    self.update_status(node="synthesize", message="Completed", status="done")
                    self.log_box.write("[Synthesizer] Answer generation complete")
        except Exception as e:
            self.log_box.write(f"[Error] Pipeline failure: {e}")
            self.update_status(node="router", message="Execution error", status="error")
        finally:
            self.running = False

    def action_clear_logs(self) -> None:
        self.log_box.clear()
        self.log_box.write("[System] Logs cleared.")

    async def action_re_ingest(self) -> None:
        if self.running:
            return
        self.running = True
        self.log_box.write("[System] Re-indexing workspace directory...")
        try:
            result = await ingest_directory(Path("."))
            self.log_box.write(f"[System] Ingestion complete: files={result.files_seen} chunks={result.chunks_created} embedded={result.embedded}")
        except Exception as e:
            self.log_box.write(f"[Error] Ingestion failed: {e}")
        finally:
            self.running = False


def main() -> None:
    RagDashboard().run()


if __name__ == "__main__":
    main()
