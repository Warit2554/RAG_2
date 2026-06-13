from __future__ import annotations

import asyncio
import os
import re
import select
import sys
from pathlib import Path

from .config import SETTINGS
from .graph import APP
from .ingest import ingest_directory

try:
    import tty
    import termios
    TERMIOS_AVAILABLE = True
except ImportError:
    TERMIOS_AVAILABLE = False


class Theme:
    def __init__(self, name: str, primary: str, secondary: str, text: str, accent: str):
        self.name = name
        self.primary = primary      # Used for titles and headers
        self.secondary = secondary  # Used for system tags (e.g. [Tool], [Interactive])
        self.text = text            # Standard text color
        self.accent = accent        # Highlight color (active selections)


class ThinkingSpinner:
    def __init__(self, theme: Theme, message: str = "nexus is thinking"):
        self.theme = theme
        self.message = message
        self.frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.task: asyncio.Task | None = None
        self._stop = False

    async def spin(self):
        idx = 0
        while not self._stop:
            frame = self.frames[idx % len(self.frames)]
            # Print a blank line, then the spinner frame, then move cursor up 1 line to stay on prompt line
            sys.stdout.write(f"\n\r{self.theme.secondary}{frame} {self.theme.primary}{self.message}...\033[0m\033[K\033[1A")
            sys.stdout.flush()
            idx += 1
            await asyncio.sleep(0.08)

    def start(self):
        if self.task is None:
            self._stop = False
            self.task = asyncio.create_task(self.spin())

    def stop(self):
        self._stop = True
        if self.task:
            self.task.cancel()
            self.task = None
        # Move down to clear spinner line, move back up to clear the blank line
        sys.stdout.write("\n\r\033[K\033[1A\033[K")
        sys.stdout.flush()


THEMES = [
    Theme("Dracula / Dark Theme", "\033[1;35m", "\033[38;5;212m", "\033[38;5;231m", "\033[30;48;5;141m"),
    Theme("Cyberpunk Theme", "\033[35m", "\033[36m", "\033[37m", "\033[30;43m"),
    Theme("Matrix Theme", "\033[32m", "\033[1;32m", "\033[32m", "\033[30;42m"),
    Theme("Retro Amber Theme", "\033[33m", "\033[1;33m", "\033[33m", "\033[30;43m"),
    Theme("Classic Theme", "\033[32m", "\033[1;36m", "\033[37m", "\033[30;42m"),
]

# Global ACTIVE_THEME reference (will be updated at start)
ACTIVE_THEME = THEMES[4]

SUGGESTIONS = [
    ("/model", "Select Ollama LLM chat model"),
    ("/embedding", "Select Ollama embedding model"),
    ("/theme", "Select CLI color theme"),
    ("/ingest", "Re-index workspace files"),
    ("/interactive", "Configure interactive mode"),
    ("/clear", "Reset conversation history"),
    ("/exit", "Quit RAG CLI"),
]


def visible_len(s: str) -> int:
    """Returns the visible length of a string, ignoring ANSI escape sequences."""
    return len(re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', s))


def save_env_setting(key: str, value: str) -> None:
    """Saves the chosen configuration variable in the local .env configuration file."""
    env_path = Path(".env")
    if not env_path.exists():
        env_path = Path("../.env")
        if not env_path.exists():
            return
            
    try:
        content = env_path.read_text(encoding="utf-8")
        pattern = rf"{key}=.*"
        replacement = f"{key}={value}"
        if f"{key}=" in content:
            new_content = re.sub(pattern, replacement, content)
        else:
            new_content = content.rstrip() + f"\n{replacement}\n"
        env_path.write_text(new_content, encoding="utf-8")
    except Exception:
        pass


def save_theme_setting(theme_name: str) -> None:
    """Saves the chosen theme variable in the local .env configuration file."""
    save_env_setting("NEXUS_THEME", theme_name)


def load_theme() -> Theme:
    """Loads the theme matching the SETTINGS configuration."""
    saved_name = SETTINGS.nexus_theme
    for t in THEMES:
        if t.name.lower() == saved_name.lower():
            return t
    return THEMES[4] # Fallback to Classic Theme


def load_command_history() -> list[str]:
    """Loads command history from ~/.nexus_history."""
    history_file = Path("~/.nexus_history").expanduser()
    if history_file.exists():
        try:
            lines = history_file.read_text(encoding="utf-8").splitlines()
            return [line.strip() for line in lines if line.strip()][-100:]
        except Exception:
            pass
    return []


def save_command_history(history: list[str]) -> None:
    """Saves last 100 command history items to ~/.nexus_history."""
    history_file = Path("~/.nexus_history").expanduser()
    try:
        history_file.write_text("\n".join(history[-100:]) + "\n", encoding="utf-8")
    except Exception:
        pass


def _get_input_with_timeout(timeout_seconds: int) -> str | None:
    ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
    if ready:
        return sys.stdin.readline().rstrip('\r\n')
    return None


def read_key(fd: int) -> str:
    """Reads a keypress from standard input file descriptor, parsing ANSI sequences robustly."""
    b = os.read(fd, 1)
    if not b:
        return ""
    if b == b'\x1b':
        # Distinguish single Escape key from arrow sequence
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            return "\x1b"
        seq = b
        b2 = os.read(fd, 1)
        seq += b2
        if b2 == b'[':
            while True:
                b_char = os.read(fd, 1)
                seq += b_char
                # CSI sequences terminate with a byte in range 0x40 - 0x7E
                if 0x40 <= b_char[0] <= 0x7E:
                    break
        return seq.decode('utf-8', errors='ignore')
    return b.decode('utf-8', errors='ignore')


def get_key_sync() -> str:
    """Reads a single keypress (including arrow escape sequences) from stdin without buffering."""
    if not TERMIOS_AVAILABLE or not sys.stdin.isatty():
        return sys.stdin.read(1)
        
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return read_key(fd)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def clear_suggestions(prev_lines_drawn: int, prompt_visible_len: int, cursor_pos: int):
    """Erase previous autocomplete dropdown lines to prevent layout overlap."""
    if prev_lines_drawn > 0:
        for _ in range(prev_lines_drawn):
            sys.stdout.write("\n\r\033[K")
        sys.stdout.write(f"\033[{prev_lines_drawn}A")
        col = prompt_visible_len + cursor_pos
        sys.stdout.write("\r")
        if col > 0:
            sys.stdout.write(f"\033[{col}C")
        sys.stdout.flush()


def draw_suggestions(matches: list[tuple[str, str]], selected_idx: int, theme: Theme, prompt_visible_len: int, cursor_pos: int) -> int:
    """Draw the autocompletion dropdown menu below the cursor using relative positioning."""
    if not matches:
        return 0
    lines_drawn = 0
    for idx, (cmd, desc) in enumerate(matches[:5]):
        sys.stdout.write("\n\r")
        lines_drawn += 1
        if idx == selected_idx:
            sys.stdout.write(f"  {theme.accent}▶ {cmd:<15} — {desc:<40}\033[0m\033[K")
        else:
            sys.stdout.write(f"  \033[90m  {cmd:<15} — {desc:<40}\033[0m\033[K")
    sys.stdout.write(f"\033[{lines_drawn}A")
    col = prompt_visible_len + cursor_pos
    sys.stdout.write("\r")
    if col > 0:
        sys.stdout.write(f"\033[{col}C")
    sys.stdout.flush()
    return lines_drawn


def interactive_select(options: list[str], title: str, default_index: int = 0, theme: Theme = None) -> int:
    """Renders a fully interactive Arrow-key selection menu."""
    if not theme:
        theme = ACTIVE_THEME
        
    if not TERMIOS_AVAILABLE or not sys.stdin.isatty():
        print(f"{title}")
        for idx, option in enumerate(options):
            print(f"  {idx + 1}) {option}")
        try:
            choice = input("Enter selection (number): ").strip()
            val = int(choice) - 1
            if 0 <= val < len(options):
                return val
        except Exception:
            pass
        return default_index

    selected_idx = default_index
    num_options = len(options)

    # Hide cursor
    sys.stdout.write("\033[?25l")
    sys.stdout.flush()

    def draw_menu():
        sys.stdout.write(f"\r{theme.primary}{title}\033[0m\n")
        for idx, option in enumerate(options):
            if idx == selected_idx:
                sys.stdout.write(f"  {theme.accent}▶ {option:<65}\033[0m\033[K\n")
            else:
                sys.stdout.write(f"    \033[90m{option:<65}\033[0m\033[K\n")
        sys.stdout.write(f"\033[{num_options + 1}A")
        sys.stdout.flush()

    try:
        draw_menu()
        while True:
            key = get_key_sync()
            if key == "\x03": # Ctrl+C
                sys.stdout.write(f"\033[{num_options + 1}B\n")
                sys.stdout.flush()
                raise KeyboardInterrupt
            elif key in ("\r", "\n"):
                # Clean up the menu
                for _ in range(num_options + 1):
                    sys.stdout.write("\r\033[K\n")
                sys.stdout.write(f"\033[{num_options + 1}A\r")
                sys.stdout.flush()
                return selected_idx
            elif key == "\x1b[A": # Arrow Up
                selected_idx = (selected_idx - 1) % num_options
                draw_menu()
            elif key == "\x1b[B": # Arrow Down
                selected_idx = (selected_idx + 1) % num_options
                draw_menu()
    finally:
        sys.stdout.write("\033[?25h")
        sys.stdout.flush()


def read_paste(fd: int) -> str:
    """Reads all immediately available characters from fd as a single pasted string."""
    chars = []
    while True:
        r, _, _ = select.select([fd], [], [], 0.005)
        if r:
            b = os.read(fd, 1)
            if b:
                chars.append(b)
            else:
                break
        else:
            break
    if not chars:
        return ""
    return b"".join(chars).decode('utf-8', errors='ignore')


def segments_to_strings(segments: list[dict], theme: Theme) -> tuple[str, str, list[tuple[int, int, dict]]]:
    """
    Returns:
      - display_string (with ANSI theme styling for placeholders)
      - actual_string (raw text value)
      - mapping: list of tuples (start_idx, end_idx, segment_ref) in display_string
    """
    display_parts = []
    actual_parts = []
    mapping = []
    
    current_display_idx = 0
    for seg in segments:
        if seg["type"] == "text":
            val = seg["value"]
            display_val = ""
            for char in val:
                if char in ("\n", "\r"):
                    display_val += f"{theme.secondary}↵\033[0m"
                else:
                    display_val += char
            display_parts.append(display_val)
            actual_parts.append(val)
            
            start = current_display_idx
            end = current_display_idx + len(val)
            mapping.append((start, end, seg))
            current_display_idx = end
        elif seg["type"] == "paste":
            placeholder = seg["placeholder"]
            styled_placeholder = f"{theme.secondary}{placeholder}\033[0m"
            display_parts.append(styled_placeholder)
            actual_parts.append(seg["value"])
            
            start = current_display_idx
            end = current_display_idx + len(placeholder)
            mapping.append((start, end, seg))
            current_display_idx = end
            
    return "".join(display_parts), "".join(actual_parts), mapping


def clean_segments(segments: list[dict]):
    """Normalizes the segments list by merging consecutive text blocks and removing empty ones."""
    new_segs = []
    for seg in segments:
        if seg["type"] == "text":
            if not seg["value"]:
                continue
            if new_segs and new_segs[-1]["type"] == "text":
                new_segs[-1]["value"] += seg["value"]
            else:
                new_segs.append(seg)
        else:
            new_segs.append(seg)
    if not new_segs:
        new_segs = [{"type": "text", "value": ""}]
    segments[:] = new_segs


def insert_char_at(segments: list[dict], cursor_pos: int, char: str, mapping: list) -> int:
    """Inserts a character (or string) at cursor_pos, updating segments in place. Returns new cursor_pos."""
    if not segments:
        segments.append({"type": "text", "value": char})
        return len(char)
        
    for idx, (start, end, seg) in enumerate(mapping):
        if seg["type"] == "text" and start <= cursor_pos <= end:
            offset = cursor_pos - start
            seg["value"] = seg["value"][:offset] + char + seg["value"][offset:]
            return cursor_pos + len(char)
        elif seg["type"] == "paste":
            if cursor_pos == start:
                segments.insert(idx, {"type": "text", "value": char})
                return cursor_pos + len(char)
            elif cursor_pos == end:
                if idx + 1 < len(segments) and segments[idx + 1]["type"] == "text":
                    segments[idx + 1]["value"] = char + segments[idx + 1]["value"]
                else:
                    segments.insert(idx + 1, {"type": "text", "value": char})
                return cursor_pos + len(char)
                
    if segments[-1]["type"] == "text":
        segments[-1]["value"] += char
    else:
        segments.append({"type": "text", "value": char})
    return cursor_pos + len(char)


def insert_paste(segments: list[dict], cursor_pos: int, paste_text: str, mapping: list, placeholder_id: int) -> int:
    """Inserts a pasted block as a single PasteBlock segment. Returns new cursor_pos."""
    lines = len(paste_text.splitlines())
    if lines > 1:
        placeholder = f"[Pasted text #{placeholder_id} +{lines - 1} lines]"
    else:
        placeholder = f"[Pasted text #{placeholder_id}]"
        
    new_seg = {"type": "paste", "value": paste_text, "placeholder": placeholder}
    
    if not segments:
        segments.append(new_seg)
        return len(placeholder)
        
    for idx, (start, end, seg) in enumerate(mapping):
        if seg["type"] == "text" and start <= cursor_pos <= end:
            offset = cursor_pos - start
            left_val = seg["value"][:offset]
            right_val = seg["value"][offset:]
            
            del segments[idx]
            insert_idx = idx
            if right_val:
                segments.insert(insert_idx, {"type": "text", "value": right_val})
            segments.insert(insert_idx, new_seg)
            if left_val:
                segments.insert(insert_idx, {"type": "text", "value": left_val})
            return cursor_pos + len(placeholder)
        elif seg["type"] == "paste":
            if cursor_pos == start:
                segments.insert(idx, new_seg)
                return cursor_pos + len(placeholder)
            elif cursor_pos == end:
                segments.insert(idx + 1, new_seg)
                return cursor_pos + len(placeholder)
                
    segments.append(new_seg)
    return cursor_pos + len(placeholder)


def handle_backspace(segments: list[dict], cursor_pos: int, mapping: list) -> int:
    """Handles backspace at cursor_pos, expanding paste blocks or deleting characters."""
    if cursor_pos <= 0 or not segments:
        return cursor_pos
        
    for idx, (start, end, seg) in enumerate(mapping):
        if start <= cursor_pos - 1 < end:
            if seg["type"] == "paste":
                # Expand paste block to text segment
                seg["type"] = "text"
                new_pos = start + len(seg["value"])
                return new_pos
            else:
                # Normal text backspace
                offset = cursor_pos - 1 - start
                seg["value"] = seg["value"][:offset] + seg["value"][offset + 1:]
                return cursor_pos - 1
                
    return cursor_pos


def read_bracketed_paste(fd: int) -> str:
    """Reads stdin until the bracketed paste end sequence \\x1b[201~ is encountered."""
    buffer = b""
    end_seq = b"\x1b[201~"
    while True:
        r, _, _ = select.select([fd], [], [], 0.05)
        if not r:
            break
        b = os.read(fd, 1)
        if not b:
            break
        buffer += b
        if buffer.endswith(end_seq):
            buffer = buffer[:-len(end_seq)]
            break
    return buffer.decode('utf-8', errors='ignore')


def get_user_input(prompt: str, theme: Theme, history: list[str] = None) -> str:
    """Reads a line of user input, supporting autocomplete overlay and arrow key selection."""
    if not TERMIOS_AVAILABLE or not sys.stdin.isatty():
        return input(prompt).strip()

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    
    segments = [{"type": "text", "value": ""}]
    cursor_pos = 0
    prev_lines_drawn = 0
    selected_idx = 0
    history_idx = len(history) if history else 0
    prompt_len = visible_len(prompt)
    is_history_browsing = False
    paste_id_counter = 1
    
    sys.stdout.write("\r" + prompt + "\033[K")
    sys.stdout.flush()
    
    try:
        tty.setraw(fd)
        sys.stdout.write("\033[?2004h") # Enable bracketed paste
        sys.stdout.flush()
        while True:
            key = read_key(fd)
            if not key:
                break
                
            full_paste = None
            if key == "\x1b[200~":
                full_paste = read_bracketed_paste(fd)
            else:
                # Check if this is part of a fast copy-paste stream (fallback)
                r, _, _ = select.select([fd], [], [], 0.01)
                if r:
                    paste_tail = read_paste(fd)
                    if paste_tail:
                        full_paste = key + paste_tail
                        
            if full_paste is not None:
                clear_suggestions(prev_lines_drawn, prompt_len, cursor_pos)
                prev_lines_drawn = 0
                
                display_str, actual_str, mapping = segments_to_strings(segments, theme)
                
                # If paste is long or multiline, insert as paste segment, else insert as text
                if len(full_paste) > 50 or "\n" in full_paste or "\r" in full_paste:
                    cursor_pos = insert_paste(segments, cursor_pos, full_paste, mapping, paste_id_counter)
                    paste_id_counter += 1
                else:
                    cursor_pos = insert_char_at(segments, cursor_pos, full_paste, mapping)
                    
                clean_segments(segments)
                is_history_browsing = False
                
                display_str, actual_str, mapping = segments_to_strings(segments, theme)
                sys.stdout.write("\r" + prompt + display_str + "\033[K")
                left_move = visible_len(display_str) - cursor_pos
                if left_move > 0:
                    sys.stdout.write(f"\033[{left_move}D")
                sys.stdout.flush()
                continue

            clear_suggestions(prev_lines_drawn, prompt_len, cursor_pos)
            prev_lines_drawn = 0
            
            display_str, actual_str, mapping = segments_to_strings(segments, theme)
            
            if key == "\x03": # Ctrl+C
                raise KeyboardInterrupt
            elif key == "\x04": # Ctrl+D
                raise EOFError
            elif key in ("\r", "\n"):
                matches = [s for s in SUGGESTIONS if s[0].startswith(actual_str)] if actual_str.startswith("/") else []
                if matches and not is_history_browsing:
                    segments = [{"type": "text", "value": matches[selected_idx][0]}]
                    display_str, actual_str, mapping = segments_to_strings(segments, theme)
                break
            elif key == "\t":
                is_history_browsing = False
                matches = [s for s in SUGGESTIONS if s[0].startswith(actual_str)] if actual_str.startswith("/") else []
                if matches:
                    segments = [{"type": "text", "value": matches[selected_idx][0]}]
                    display_str, actual_str, mapping = segments_to_strings(segments, theme)
                    cursor_pos = visible_len(display_str)
                sys.stdout.write("\r" + prompt + display_str + "\033[K")
                sys.stdout.flush()
            elif key in ("\x7f", "\x08"):
                is_history_browsing = False
                cursor_pos = handle_backspace(segments, cursor_pos, mapping)
                clean_segments(segments)
                
                display_str, actual_str, mapping = segments_to_strings(segments, theme)
                sys.stdout.write("\r" + prompt + display_str + "\033[K")
                left_move = visible_len(display_str) - cursor_pos
                if left_move > 0:
                    sys.stdout.write(f"\033[{left_move}D")
                sys.stdout.flush()
            elif key == "\x1b[C":
                if cursor_pos < visible_len(display_str):
                    cursor_pos += 1
                    sys.stdout.write("\033[1C")
                    sys.stdout.flush()
            elif key == "\x1b[D":
                if cursor_pos > 0:
                    cursor_pos -= 1
                    sys.stdout.write("\033[1D")
                    sys.stdout.flush()
            elif key == "\x1b[A":
                matches = [s for s in SUGGESTIONS if s[0].startswith(actual_str)] if actual_str.startswith("/") else []
                if matches and not is_history_browsing:
                    selected_idx = (selected_idx - 1) % min(len(matches), 5)
                else:
                    is_history_browsing = True
                    if history and history_idx > 0:
                        history_idx -= 1
                        segments = [{"type": "text", "value": history[history_idx]}]
                        display_str, actual_str, mapping = segments_to_strings(segments, theme)
                        cursor_pos = visible_len(display_str)
                        sys.stdout.write("\r" + prompt + display_str + "\033[K")
                        sys.stdout.flush()
            elif key == "\x1b[B":
                matches = [s for s in SUGGESTIONS if s[0].startswith(actual_str)] if actual_str.startswith("/") else []
                if matches and not is_history_browsing:
                    selected_idx = (selected_idx + 1) % min(len(matches), 5)
                else:
                    is_history_browsing = True
                    if history and history_idx < len(history) - 1:
                        history_idx += 1
                        segments = [{"type": "text", "value": history[history_idx]}]
                        display_str, actual_str, mapping = segments_to_strings(segments, theme)
                        cursor_pos = visible_len(display_str)
                        sys.stdout.write("\r" + prompt + display_str + "\033[K")
                        sys.stdout.flush()
                    elif history and history_idx == len(history) - 1:
                        history_idx += 1
                        segments = [{"type": "text", "value": ""}]
                        display_str, actual_str, mapping = segments_to_strings(segments, theme)
                        cursor_pos = 0
                        sys.stdout.write("\r" + prompt + display_str + "\033[K")
                        sys.stdout.flush()
            elif len(key) == 1 and key.isprintable():
                is_history_browsing = False
                cursor_pos = insert_char_at(segments, cursor_pos, key, mapping)
                clean_segments(segments)
                
                display_str, actual_str, mapping = segments_to_strings(segments, theme)
                sys.stdout.write("\r" + prompt + display_str + "\033[K")
                left_move = visible_len(display_str) - cursor_pos
                if left_move > 0:
                    sys.stdout.write(f"\033[{left_move}D")
                sys.stdout.flush()
                selected_idx = 0

            # Draw suggestions
            if actual_str.startswith("/") and not is_history_browsing:
                matches = [s for s in SUGGESTIONS if s[0].startswith(actual_str)]
                if matches:
                    prev_lines_drawn = draw_suggestions(matches, selected_idx, theme, prompt_len, cursor_pos)
                    
    finally:
        sys.stdout.write("\033[?2004l") # Disable bracketed paste
        sys.stdout.flush()
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        
    display_str, actual_str, mapping = segments_to_strings(segments, theme)
    sys.stdout.write("\r" + prompt + display_str + "\033[K\n")
    sys.stdout.flush()
    return actual_str


async def get_ollama_models() -> list[dict]:
    """Dynamically fetches the list of installed models from local Ollama service."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{SETTINGS.ollama_host.rstrip('/')}/api/tags")
            if response.status_code == 200:
                return response.json().get("models", [])
    except Exception:
        pass
    return []


class CliRepl:
    def __init__(self) -> None:
        self.history: list[dict[str, str]] = []
        self.command_history: list[str] = load_command_history()
        self.interactive_mode = "strict"
        self.interactive_timeout = 60

    async def ingest(self) -> None:
        theme = ACTIVE_THEME
        print(f"\n{theme.secondary}[Ingestion]\033[0m Indexing workspace directory...")
        try:
            result = await ingest_directory(Path("."))
            print(
                f"{theme.secondary}[Ingestion]\033[0m Success! Files seen: {result.files_seen}, "
                f"Chunks: {result.chunks_created}, Embedded: {result.embedded}"
            )
        except Exception as e:
            print(f"\033[31m[Ingestion] Error:\033[0m {e}")

    async def start(self) -> None:
        # Load Theme from Settings
        global ACTIVE_THEME
        ACTIVE_THEME = load_theme()
        theme = ACTIVE_THEME

        print("\033[1;36m" + "─"*60)
        print("NEXUS LOCAL RAG SYSTEM (Antigravity Engine)".center(60))
        print("─"*60 + "\033[0m")
        print(f"{theme.secondary}[System]{theme.text} Loaded theme: {theme.primary}{theme.name}\033[0m (type /theme to change)")
        print(f"Ollama Host: {theme.secondary}{SETTINGS.ollama_host}\033[0m | Chat Model: {theme.secondary}{SETTINGS.ollama_chat_model}\033[0m | Embed Model: {theme.secondary}{SETTINGS.ollama_embed_model}\033[0m\n")

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
        spinner: ThinkingSpinner | None = None

        def on_token(token: str) -> None:
            nonlocal first_token
            if first_token:
                if spinner:
                    spinner.stop()
                sys.stdout.write("\n\n")
                first_token = False
            current_answer.append(token)
            sys.stdout.write(f"{theme.text}{token}\033[0m")
            sys.stdout.flush()

        while True:
            try:
                try:
                    prompt_str = f"{theme.primary}nexus ›\033[0m "
                    query = get_user_input(prompt_str, theme, self.command_history)
                except EOFError:
                    raise

                if not query:
                    continue

                # Add to command history if it's not a duplicate of the last command
                if not self.command_history or self.command_history[-1] != query:
                    self.command_history.append(query)
                    save_command_history(self.command_history)

                if query in {"/exit", "/quit", "exit", "quit"}:
                    print(f"{theme.primary}Goodbye!\033[0m")
                    break

                if query == "/clear":
                    self.history = []
                    print(f"{theme.secondary}[System]\033[0m Conversation history cleared.")
                    continue

                if query == "/ingest":
                    await self.ingest()
                    print()
                    continue

                if query == "/theme":
                    options = [t.name for t in THEMES]
                    current_idx = 0
                    for idx, t in enumerate(THEMES):
                        if t.name == theme.name:
                            current_idx = idx
                            break
                            
                    idx = interactive_select(
                        options,
                        "Select a new terminal theme:",
                        default_index=current_idx,
                        theme=theme
                    )
                    theme = THEMES[idx]
                    ACTIVE_THEME = theme
                    SETTINGS.nexus_theme = theme.name
                    save_theme_setting(theme.name)
                    print(f"{theme.secondary}[System]{theme.text} Theme updated to: {theme.primary}{theme.name}\033[0m\n")
                    continue

                if query == "/model":
                    print(f"\n{theme.primary}[Model Selection]\033[0m Fetching completion models from Ollama host ({SETTINGS.ollama_host})...")
                    models = await get_ollama_models()
                    chat_models = []
                    for m in models:
                        name = m.get("name", "")
                        caps = m.get("capabilities", [])
                        if caps:
                            if "completion" in caps:
                                chat_models.append(name)
                        else:
                            if "embed" not in name.lower():
                                chat_models.append(name)
                                
                    if not chat_models:
                        print(f"\033[31m[Error]\033[0m No chat models found on local Ollama service.")
                        print()
                        continue
                        
                    idx = interactive_select(
                        chat_models,
                        "Use Arrow Up/Down & Enter to select chat model:",
                        default_index=0,
                        theme=theme
                    )
                    selected = chat_models[idx]
                    SETTINGS.ollama_chat_model = selected
                    SETTINGS.ollama_router_model = selected
                    SETTINGS.ollama_orchestrator_model = selected
                    save_env_setting("OLLAMA_CHAT_MODEL", selected)
                    save_env_setting("OLLAMA_ROUTER_MODEL", selected)
                    save_env_setting("OLLAMA_ORCHESTRATOR_MODEL", selected)
                    print(f"{theme.secondary}[Model Selection]\033[0m Chat, Router, and Orchestrator models updated to: \033[1m{selected}\033[0m\n")
                    continue

                if query == "/embedding":
                    print(f"\n{theme.primary}[Embedding Selection]\033[0m Fetching embedding models from Ollama host ({SETTINGS.ollama_host})...")
                    models = await get_ollama_models()
                    embed_models = []
                    for m in models:
                        name = m.get("name", "")
                        caps = m.get("capabilities", [])
                        if caps:
                            if "embedding" in caps:
                                embed_models.append(name)
                        else:
                            if "embed" in name.lower():
                                embed_models.append(name)
                                
                    if not embed_models:
                        print(f"\033[31m[Error]\033[0m No embedding models found on local Ollama service.")
                        print()
                        continue
                        
                    idx = interactive_select(
                        embed_models,
                        "Use Arrow Up/Down & Enter to select embedding model:",
                        default_index=0,
                        theme=theme
                    )
                    selected = embed_models[idx]
                    SETTINGS.ollama_embed_model = selected
                    save_env_setting("OLLAMA_EMBED_MODEL", selected)
                    print(f"{theme.secondary}[Embedding Selection]\033[0m Embedding model updated to: \033[1m{selected}\033[0m\n")
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
                            print(f"{theme.secondary}[System]\033[0m Interactive mode set to: {mode}" + (f" (timeout={self.interactive_timeout}s)" if mode == "timeout" else ""))
                        else:
                            print(f"\033[31m[Error]\033[0m Invalid interactive mode. Use: strict, timeout, or auto.")
                    else:
                        print(f"{theme.secondary}[System]\033[0m Current interactive mode: {self.interactive_mode}" + (f" (timeout={self.interactive_timeout}s)" if self.interactive_mode == "timeout" else ""))
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
                    spinner = ThinkingSpinner(theme, "nexus is analyzing your request")
                    spinner.start()
                    try:
                        async for update in APP.astream(
                            graph_input,
                            config=config,
                            stream_mode="updates",
                        ):
                            spinner.stop()
                            if "plan" in update:
                                data = update["plan"]
                                plan = data.get("plan")
                                if plan and plan.tasks:
                                    for t in plan.tasks:
                                        print(f"{theme.secondary}[Tool]\033[0m Running {t.kind} task: {t.query}")
                            elif "retrieve" in update:
                                print(f"{theme.secondary}[Tool]\033[0m Running retrieve task: Searching indexed workspace files")
                            elif "synthesize" in update:
                                synth_data = update["synthesize"]
                                if not synth_data.get("clarification_prompt"):
                                    final_ans = synth_data.get("final_answer", "")
                                    if final_ans and not current_answer:
                                        print(f"\n\n{theme.text}{final_ans}\033[0m")
                                        current_answer.append(final_ans)

                            for node_name, node_state in update.items():
                                final_state.update(node_state)

                            # Update spinner message based on current stage of the graph
                            if "plan" in update:
                                spinner.message = "nexus is executing tool plan"
                            elif "retrieve" in update:
                                spinner.message = "nexus is synthesizing response"
                            else:
                                spinner.message = "nexus is thinking"

                            if first_token:
                                spinner.start()

                    except Exception as e:
                        spinner.stop()
                        print(f"\n\033[31m[Error] Pipeline failure:\033[0m {e}\n")
                        break
                    finally:
                        spinner.stop()

                    prompt = final_state.get("clarification_prompt")
                    if prompt and not clarification_response:
                        current_answer.clear()

                        if self.interactive_mode == "auto":
                            default_path = prompt["paths"][prompt["default_index"]]
                            print(f"\n{theme.secondary}[Interactive]\033[0m (Auto Mode) Automatically selecting default path: {default_path}")
                            clarification_response = default_path
                            continue

                        # If timeout mode is active, fallback to input with timeout
                        if self.interactive_mode == "timeout":
                            print(f"\n{theme.primary}[Interactive Clarification]\033[0m {prompt['question']}")
                            for idx, option in enumerate(prompt["options"], start=1):
                                rec = " (Most Recommended)" if (idx - 1) == prompt["default_index"] else ""
                                print(f" {idx}){rec} {option}")
                            print(f" 3) Enter custom answer")

                            print(f"Please choose (1-3) within {self.interactive_timeout}s [Default is 1]: ", end="", flush=True)
                            user_choice = _get_input_with_timeout(self.interactive_timeout)
                            if user_choice is None:
                                print(f"\n{theme.secondary}[Interactive]\033[0m Timeout reached. Auto-selecting Option 1.")
                                user_choice = "1"
                            
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
                        else:
                            # Otherwise, display beautiful Arrow Up/Down selection
                            opts = []
                            for idx, option in enumerate(prompt["options"]):
                                rec = " (Most Recommended)" if idx == prompt.get("default_index", 0) else ""
                                opts.append(f"{option}{rec}")
                            opts.append("Enter custom answer")

                            selected_idx = interactive_select(
                                opts,
                                f"[Interactive Clarification] {prompt['question']}",
                                default_index=prompt.get("default_index", 0),
                                theme=theme
                            )

                            if selected_idx < len(prompt["options"]):
                                clarification_response = prompt["paths"][selected_idx]
                            else:
                                custom_path = input("Enter custom absolute path: ").strip()
                                clarification_response = custom_path

                        print(f"{theme.secondary}[Interactive]\033[0m Selected: {clarification_response}")
                        continue
                    else:
                        break

                # Append completed exchange to history
                answer_text = "".join(current_answer).strip()
                if answer_text and not final_state.get("clarification_prompt"):
                    self.history.append({"role": "user", "content": query})
                    self.history.append({"role": "assistant", "content": answer_text})
                print("\n")

            except KeyboardInterrupt:
                if spinner:
                    spinner.stop()
                sys.stdout.write("\n")
                sys.stdout.flush()
                continue
            except EOFError:
                print(f"\n{theme.primary}Goodbye!\033[0m")
                break


def main() -> None:
    repl = CliRepl()
    try:
        asyncio.run(repl.start())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
