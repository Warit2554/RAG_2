from __future__ import annotations

import asyncio
import select
import sys
from pathlib import Path

from .config import SETTINGS
from .graph import APP
from .ingest import ingest_directory


def _get_input_with_timeout(timeout_seconds: int) -> str | None:
    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if ready:
        return sys.stdin.readline().rstrip('\r\n')
    return None


class CliRepl:
    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []
        self.interactive_mode = "strict"
        self.interactive_timeout = 60

    async def ingest(self) -> None:
        print("\n\033[32m[Ingestion]\033[0m Indexing workspace directory...")
        try:
            result = await ingest_directory(Path("."))
            print(
                f"\033[32m[Ingestion]\033[0m Success! Files seen: {result.files_seen}, "
                f"Chunks: {result.chunks_created}, Embedded: {result.embedded}"
            )
        except Exception as e:
            print(f"\033[31m[Ingestion] Error:\033[0m {e}")

    async def start(self) -> None:
        print("\033[1;32mLocal RAG CLI REPL (type /exit to quit, /clear to reset history, /ingest to re-index, /interactive to configure)\033[0m")
        print(f"Ollama Host: {SETTINGS.ollama_host} | Model: {SETTINGS.ollama_chat_model}\n")

        from .mcp_client import mcp_manager
        import atexit

        mcp_manager._started = True
        await mcp_manager.start_all()

        def cleanup_mcp():
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(mcp_manager.stop_all())
                else:
                    loop.run_until_complete(mcp_manager.stop_all())
            except Exception:
                try:
                    asyncio.run(mcp_manager.stop_all())
                except Exception:
                    pass
        atexit.register(cleanup_mcp)

        print()

        current_answer = []
        first_token = True

        def on_token(token: str) -> None:
            nonlocal first_token
            if first_token:
                sys.stdout.write("\n\n")
                first_token = False
            current_answer.append(token)
            sys.stdout.write(token)
            sys.stdout.flush()

        while True:
            try:
                query = input("\033[1;32mnexus >\033[0m ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nGoodbye!")
                break

            if not query:
                continue

            if query in {"/exit", "/quit", "exit", "quit"}:
                print("Goodbye!")
                break

            if query == "/clear":
                self.history = []
                print("Conversation history cleared.")
                continue

            if query == "/ingest":
                await self.ingest()
                print()
                continue

            if query.startswith("/interactive"):
                parts = query.split()
                if len(parts) >= 2:
                    mode = parts[1].lower()
                    if mode in {"strict", "timeout", "auto"}:
                        self.interactive_mode = mode
                        if mode == "timeout" and len(parts) >= 3:
                            try:
                                self.interactive_timeout = int(parts[2])
                            except ValueError:
                                pass
                        print(f"Interactive mode set to: {mode}" + (f" (timeout={self.interactive_timeout}s)" if mode == "timeout" else ""))
                    else:
                        print("Invalid interactive mode. Use: strict, timeout, or auto.")
                else:
                    print(f"Current interactive mode: {self.interactive_mode}" + (f" (timeout={self.interactive_timeout}s)" if self.interactive_mode == "timeout" else ""))
                print()
                continue

            clarification_response = None
            while True:
                current_answer.clear()
                first_token = True
                config = {
                    "configurable": {
                        "token_callback": on_token
                    }
                }

                graph_input = {"user_input": query, "chat_history": self.history}
                if clarification_response:
                    graph_input["clarification_response"] = clarification_response

                final_state = {}
                try:
                    async for update in APP.astream(
                        graph_input,
                        config=config,
                        stream_mode="updates",
                    ):
                        if "plan" in update:
                            data = update["plan"]
                            plan = data.get("plan")
                            if plan and plan.tasks:
                                for t in plan.tasks:
                                    print(f"\n\033[33m[Tool]\033[0m Running {t.kind} task: {t.query}")
                        elif "retrieve" in update:
                            print(f"\n\033[33m[Tool]\033[0m Running retrieve task: Searching indexed workspace files")
                        elif "synthesize" in update:
                            synth_data = update["synthesize"]
                            # Skip printing if this is a clarification turn (REPL handles it)
                            if not synth_data.get("clarification_prompt"):
                                final_ans = synth_data.get("final_answer", "")
                                if final_ans and not current_answer:
                                    print(f"\n\n{final_ans}")
                                    current_answer.append(final_ans)

                        for node_name, node_state in update.items():
                            final_state.update(node_state)

                except Exception as e:
                    print(f"\n\033[31m[Error] Pipeline failure:\033[0m {e}\n")
                    break

                prompt = final_state.get("clarification_prompt")
                if prompt and not clarification_response:
                    current_answer.clear()

                    if self.interactive_mode == "auto":
                        default_path = prompt["paths"][prompt["default_index"]]
                        print(f"\n\033[33m[Interactive]\033[0m (Auto Mode) Automatically selecting default path: {default_path}")
                        clarification_response = default_path
                        continue

                    print(f"\n\033[32mnexus >\033[0m {prompt['question']}")
                    for idx, option in enumerate(prompt["options"], start=1):
                        rec = " (Most Recommended)" if (idx - 1) == prompt["default_index"] else ""
                        print(f" {idx}){rec} {option}")
                    print(f" 3) Enter custom answer")

                    user_choice = None
                    if self.interactive_mode == "timeout":
                        print(f"Please choose (1-3) within {self.interactive_timeout}s [Default is 1]: ", end="", flush=True)
                        user_choice = _get_input_with_timeout(self.interactive_timeout)
                        if user_choice is None:
                            print(f"\n\033[33m[Interactive]\033[0m Timeout reached. Auto-selecting Option 1.")
                            user_choice = "1"
                    else:
                        user_choice = input("Please choose (1-3): ").strip()

                    if not user_choice:
                        user_choice = "1"

                    if user_choice == "1":
                        clarification_response = prompt["paths"][0]
                    elif user_choice == "2":
                        clarification_response = prompt["paths"][1]
                    else:
                        if user_choice == "3":
                            custom_path = input("Enter custom absolute path: ").strip()
                        else:
                            custom_path = user_choice.strip()
                        clarification_response = custom_path

                    print(f"\n\033[32m[Interactive]\033[0m Selected: {clarification_response}")
                    continue
                else:
                    break

            # Append completed exchange to history
            answer_text = "".join(current_answer).strip()
            if answer_text and not final_state.get("clarification_prompt"):
                self.history.append({"role": "user", "content": query})
                self.history.append({"role": "assistant", "content": answer_text})
            print("\n")


def main() -> None:
    repl = CliRepl()
    try:
        asyncio.run(repl.start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
