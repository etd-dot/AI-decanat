"""Native Windows window for the two-dimensional voice assistant."""

from __future__ import annotations

import queue
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk

from voice_avatar import VoiceAvatar
from voice_service import VoiceService

PAPER = "#f4f0e7"
PANEL = "#fffdf8"
INK = "#203129"
MUTED = "#586a60"
LINE = "#ddd9cd"
GREEN = "#244739"
PALE = "#e5eee4"
FONT = "Segoe UI"
DEFAULT_DEVICE = "По умолчанию в Windows"
JOURNAL_PAGE_SIZE = 20
STATES = {
    "idle": "Готов к разговору",
    "standing_up": "Начинаю разговор",
    "listening": "Слушаю вас",
    "transcribing": "Распознаю вопрос",
    "thinking": "Готовлю ответ",
    "speaking": "Отвечаю",
    "sitting_down": "Завершаю разговор",
}


class VoiceWindow:
    def __init__(self, window: tk.Tk, service: VoiceService, art: Path, *, kiosk: bool = False) -> None:
        self.window = window
        self.service = service
        self.kiosk = kiosk
        self.health = {}
        self.admin = False
        self.provider_settings = None
        self.audio_settings = None
        self.journal_page = None
        self.busy = False
        self.online = False
        self.state = "idle"
        self.session_id = None
        self.evidence = []
        self.sources_popup = None
        self.warning_seconds = 0
        self.settings = None
        self.hint = "Запускаю голосовой сервис…"
        self.art = art

        window.title("AI-деканат")
        window.geometry("1070x690")
        window.minsize(950, 635)
        window.configure(bg=PAPER)
        if kiosk:
            window.attributes("-fullscreen", True)
            window.protocol("WM_DELETE_WINDOW", lambda: None)
            window.bind("<Control-Alt-a>", self.open_staff_keyboard)
            window.bind("<Control-Alt-A>", self.open_staff_keyboard)
            window.bind("<Control-Alt-F10>", self.open_staff_keyboard)
        else:
            window.protocol("WM_DELETE_WINDOW", window.destroy)
        self.build()
        window.bind("<Destroy>", self.stop_poll, add="+")
        self.poll_job = window.after(20, self.poll)

    def label(self, parent, text="", *, size=11, color=INK, bg=PANEL, bold=False, **options):
        return tk.Label(parent, text=text, font=(FONT, size, "bold" if bold else "normal"),
                        bg=bg, fg=color, anchor="w", justify="left", **options)

    def button(self, parent, text, command, *, primary=False, bg=PANEL):
        return tk.Button(
            parent, text=text, command=command, font=(FONT, 10, "bold"),
            bg=GREEN if primary else bg, fg="white" if primary else GREEN,
            activebackground="#385b4b" if primary else PALE,
            activeforeground="white" if primary else INK, disabledforeground="#7c8c80",
            relief="flat", borderwidth=0, padx=15, pady=10, cursor="hand2",
            highlightthickness=1, highlightcolor=GREEN, highlightbackground=GREEN if primary else LINE,
        )

    def build(self):
        header = tk.Frame(self.window, bg=PAPER)
        header.pack(fill="x", padx=30, pady=(22, 18))
        self.label(header, "AI-деканат", size=21, bg=PAPER, bold=True).pack(side="left")
        self.settings_button = self.button(header, "Настройки", self.open_settings, bg=PAPER)
        if not self.kiosk:
            self.settings_button.pack(side="right")
        self.mode = self.label(header, bg=PAPER, color=MUTED)
        self.mode.pack(side="right", padx=18)

        body = tk.Frame(self.window, bg=PAPER)
        body.pack(fill="both", expand=True, padx=30, pady=(0, 24))
        visual = tk.Frame(body, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        visual.pack(side="left", fill="y", padx=(0, 16))
        self.avatar = VoiceAvatar(visual, self.art)
        self.avatar.pack()
        self.status = self.label(visual, size=13, bold=True)
        self.status.pack(fill="x", padx=18, pady=(13, 3))
        self.detail = self.label(visual, size=9, color=MUTED, wraplength=380)
        self.detail.pack(fill="x", padx=18, pady=(0, 12))

        answer = tk.Frame(body, bg=PANEL, highlightbackground=LINE, highlightthickness=1)
        answer.pack(side="left", fill="both", expand=True)
        inside = tk.Frame(answer, bg=PANEL)
        inside.pack(fill="both", expand=True, padx=26, pady=23)
        self.label(inside, "Чем помочь?", size=27, bold=True).pack(fill="x")
        self.question = self.label(inside, "Спросите о справках или часах работы деканата.",
                                   color=MUTED, wraplength=365)
        self.question.pack(fill="x", pady=(8, 16))
        tk.Frame(inside, bg=LINE, height=1).pack(fill="x")
        self.answer = tk.Text(
            inside, wrap="word", height=4, width=15, font=(FONT, 14), spacing3=8,
            bg=PANEL, fg=INK, borderwidth=0, highlightthickness=0, padx=0, pady=14,
        )
        self.answer.pack(fill="both", expand=True)
        self.set_answer("")
        buttons = tk.Frame(inside, bg=PANEL)
        buttons.pack(fill="x", pady=(9, 0))
        self.start_button = self.button(buttons, "Начать разговор", self.start, primary=True)
        self.start_button.pack(side="left")
        self.finish_button = self.button(buttons, "Завершить", lambda: self.send("finish"))
        self.finish_button.pack(side="left", padx=6)
        self.sources_button = self.button(buttons, "Источники", self.show_sources)
        self.sources_button.pack(side="right")
        self.update_controls()

    def set_answer(self, text):
        self.answer.config(state="normal")
        self.answer.delete("1.0", "end")
        self.answer.insert("1.0", text)
        self.answer.config(state="disabled")

    def update_controls(self):
        idle = self.state == "idle"
        ready = self.health.get("setup_step") == "ready" and self.online
        self.mode.config(text="Демонстрационная база" if self.health.get("demo_knowledge") else "")
        self.status.config(text=STATES[self.state] if ready else
                           "Требуется настройка" if self.online else "Голосовой сервис")
        detail = (f"Разговор завершится через {self.warning_seconds} с." if self.warning_seconds
                  else self.hint or self.health.get("setup_issue") or "Голосовой режим")
        self.detail.config(text=detail)
        self.start_button.config(state="normal" if idle and ready and not self.busy else "disabled")
        self.finish_button.config(state="normal" if not idle and self.online else "disabled")
        self.sources_button.config(state="normal" if self.evidence else "disabled")
        self.settings_button.config(state="normal" if self.online and idle and not self.busy else "disabled")

    def start(self):
        self.hint = ""
        self.question.config(text="Дождитесь надписи «Слушаю вас» и задайте вопрос вслух.")
        self.set_answer("")
        self.evidence = []
        self.start_button.config(state="disabled")
        self.service.send({"type": "start", "avatar_configured": True})

    def send(self, name):
        self.service.send({"type": name, "session_id": self.session_id})

    def poll(self):
        self.poll_job = None
        try:
            while True:
                self.handle(*self.service.events.get_nowait())
        except queue.Empty:
            pass
        self.poll_job = self.window.after(20, self.poll)

    def stop_poll(self, event):
        if event.widget is self.window and self.poll_job is not None:
            self.window.after_cancel(self.poll_job)
            self.poll_job = None

    def handle(self, name, event):
        if name == "health":
            signed_in = self.admin
            self.health, self.admin = event
            if not self.admin:
                self.provider_settings = None
                self.audio_settings = None
                self.journal_page = None
            if not self.online:
                self.hint = ""
            self.online, self.busy = True, False
            if self.settings and self.admin != signed_in:
                self.render_settings()
            if self.settings and self.admin:
                self.update_audio_controls()
                self.update_journal_controls()
        elif name == "provider_settings":
            self.provider_settings = event
            if self.settings and self.admin:
                self.render_settings()
        elif name == "audio_settings":
            if self.settings and self.admin:
                self.audio_settings = event
                self.render_audio()
        elif name == "journal_page":
            if self.settings and self.admin:
                self.journal_page = event
                self.render_journal_page()
        elif name == "notice":
            self.hint = event
            if self.settings:
                self.settings_status.config(text=event)
        elif name == "offline":
            self.online, self.busy = False, False
            self.warning_seconds = 0
            self.hint = event
            self.avatar.reset()
            if self.settings:
                self.close_settings()
            if self.kiosk:
                self.window.after(3000, self.window.destroy)
        else:
            data = event["payload"]
            if name in {"state", "snapshot"}:
                self.state = data["state"]
                self.session_id = event["session_id"]
                if name == "snapshot" or self.state != "listening":
                    self.warning_seconds = data.get("warning_seconds", 0)
                if self.state == "idle":
                    self.avatar.reset()
                elif self.state in {"listening", "sitting_down"}:
                    self.avatar.stop_speech()
            elif name == "avatar":
                session = event["session_id"]
                self.avatar.move(data["action"] == "stand", lambda: self.service.send(
                    {"type": "avatar_ready", "session_id": session},
                ))
            elif name == "mouth_track":
                self.avatar.prepare_speech(event["turn_id"], data["shapes"], data["step"])
            elif name == "playback_clock":
                self.avatar.start_speech(event["turn_id"], data["starts_at"])
            elif name == "playback_stop":
                self.avatar.stop_speech()
            elif name == "transcript":
                self.question.config(text=data["text"])
                self.set_answer("")
                self.evidence = []
                self.close_sources()
            elif name == "answer":
                self.set_answer(data["answer"]["answer_text"])
                self.evidence = data["sources"]
            elif name in {"error", "command_error", "clarify"}:
                self.hint = data.get("message", data.get("text", ""))
            elif name == "cleared":
                self.warning_seconds = 0
                self.question.config(text="Спросите о справках или часах работы деканата.")
                self.set_answer("")
                self.evidence = []
                self.close_sources()
            elif name == "idle_warning":
                self.warning_seconds = data["seconds"] if data["visible"] else 0
        self.update_controls()

    def show_sources(self):
        if self.sources_popup:
            self.sources_popup.lift()
            return
        popup = tk.Toplevel(self.window, bg=PANEL)
        self.sources_popup = popup
        popup.title("Источники ответа")
        popup.geometry("610x400")
        popup.protocol("WM_DELETE_WINDOW", self.close_sources)
        description = tk.Text(popup, wrap="word", padx=20, pady=17, bg=PANEL, fg=INK,
                              font=(FONT, 11), relief="flat", spacing3=7)
        description.pack(fill="both", expand=True)
        for source in self.evidence:
            description.insert("end", f"{source['document_title']} · {source['locator']}\n"
                               f"{source['source_label']}\n{source['text']}\n\n")
        description.config(state="disabled")

    def close_sources(self):
        if self.sources_popup:
            self.sources_popup.destroy()
            self.sources_popup = None

    def open_settings(self):
        if self.settings:
            self.settings.lift()
            return
        self.settings = tk.Toplevel(self.window, bg=PANEL)
        self.settings.title("Настройки сотрудника")
        self.settings.geometry("600x560")
        self.settings.minsize(515, 535)
        self.settings.transient(self.window)
        self.settings.protocol("WM_DELETE_WINDOW", self.close_settings)
        self.settings_content = tk.Frame(self.settings, bg=PANEL)
        self.settings_content.pack(fill="both", expand=True, padx=23, pady=21)
        self.settings_status = self.label(self.settings, color=MUTED, wraplength=455)
        self.settings_status.pack(fill="x", padx=23, pady=(0, 18))
        self.render_settings()

    def open_staff_keyboard(self, _event):
        if self.online and self.state == "idle" and not self.busy:
            self.open_settings()
        return "break"

    def close_settings(self):
        should_logout = self.admin or self.busy
        self.settings.destroy()
        self.settings = None
        self.audio_settings = None
        self.journal_page = None
        if should_logout:
            self.admin = False
            self.provider_settings = None
            self.busy = True
            self.update_controls()
            self.service.setup("logout")

    def render_settings(self):
        for child in self.settings_content.winfo_children():
            child.destroy()
        panel = self.settings_content
        self.label(panel, "Настройки сотрудника", size=19, bold=True).pack(fill="x", pady=(0, 11))
        if not self.admin:
            creating = self.health.get("setup_step") == "password"
            if self.kiosk and creating:
                self.label(panel, "Администратор Windows должен подготовить пароль сотрудника.",
                           color=MUTED, wraplength=420).pack(fill="x")
                return
            self.label(panel, "Придумайте пароль от 10 символов." if creating else
                       "Введите пароль сотрудника.", color=MUTED).pack(fill="x", pady=(0, 6))
            password = tk.Entry(panel, show="•", font=(FONT, 11))
            password.pack(fill="x", pady=5, ipady=5)
            password.focus_set()
            confirmation = tk.Entry(panel, show="•", font=(FONT, 11))
            if creating:
                confirmation.pack(fill="x", pady=5, ipady=5)

            def submit():
                value = password.get()
                if creating and (len(value) < 10 or value != confirmation.get()):
                    self.settings_status.config(text="Введите одинаковый пароль из 10 или более символов.")
                    return
                if value:
                    self.run_setup("create" if creating else "login", value)
                    password.delete(0, "end")
                    confirmation.delete(0, "end")

            self.button(panel, "Создать пароль и войти" if creating else "Войти", submit,
                        primary=True).pack(anchor="w", pady=8)
            password.bind("<Return>", lambda _event: submit())
            confirmation.bind("<Return>", lambda _event: submit())
            return

        tabs = ttk.Notebook(panel)
        tabs.pack(fill="both", expand=True)
        provider = tk.Frame(tabs, bg=PANEL, padx=14, pady=12)
        knowledge = tk.Frame(tabs, bg=PANEL, padx=14, pady=12)
        self.audio_panel = tk.Frame(tabs, bg=PANEL, padx=14, pady=12)
        journal = tk.Frame(tabs, bg=PANEL, padx=14, pady=12)
        tabs.add(provider, text="Сервис ответов")
        tabs.add(knowledge, text="База знаний")
        tabs.add(self.audio_panel, text="Звук")
        tabs.add(journal, text="Журнал")
        self.render_provider(provider)
        self.button(knowledge, "Установить демонстрационную базу",
                    lambda: self.run_setup("demo")).pack(anchor="w", pady=3)
        self.button(knowledge, "Загрузить ZIP с базой", self.choose_knowledge).pack(anchor="w", pady=3)
        self.render_audio()
        self.build_journal(journal)

        def load_tab(_event):
            if tabs.select() == str(self.audio_panel) and self.audio_settings is None:
                self.run_setup("load_audio")
            elif tabs.select() == str(journal) and self.journal_page is None:
                self.run_setup("journal", 0)

        tabs.bind("<<NotebookTabChanged>>", load_tab)

    def render_audio(self):
        panel = self.audio_panel
        for child in panel.winfo_children():
            child.destroy()
        self.audio_values = {}
        self.audio_buttons = []
        if self.audio_settings is None:
            self.button(panel, "Загрузить устройства",
                        lambda: self.run_setup("load_audio")).pack(anchor="w")
            return
        for kind, title in (("input", "Микрофон"), ("output", "Динамики")):
            field = f"{kind}_device"
            labels = {DEFAULT_DEVICE: ""}
            for device in sorted(self.audio_settings["devices"], key=lambda row: (
                row["host_api"] != "Windows WASAPI", row["name"].casefold(),
            )):
                if device[f"selectable_{kind}"]:
                    labels[device["selection"]] = device["selection"]
            saved = self.audio_settings[field]
            selected = saved or DEFAULT_DEVICE
            if saved and saved not in labels:
                selected = f"{saved} (недоступно)"
                labels[selected] = saved
            self.label(panel, title, bold=True).pack(fill="x", pady=(0, 4))
            combo = ttk.Combobox(panel, state="readonly", values=list(labels), font=(FONT, 10))
            combo.set(selected)
            combo.pack(fill="x", pady=(0, 12))
            combo.bind("<<ComboboxSelected>>", lambda _event: self.update_audio_controls())
            self.audio_values[field] = (combo, labels)
        self.audio_hint = self.label(panel, color=MUTED, wraplength=430)
        self.audio_hint.pack(fill="x", pady=(0, 9))
        actions = tk.Frame(panel, bg=PANEL)
        actions.pack(fill="x", pady=(0, 8))
        self.audio_save = self.button(actions, "Сохранить выбор",
                                      lambda: self.run_setup("save_audio", self.selected_audio()), primary=True)
        self.audio_save.pack(side="left", padx=(0, 7))
        self.button(actions, "Обновить список", lambda: self.run_setup("load_audio")).pack(side="left")
        tests = tk.Frame(panel, bg=PANEL)
        tests.pack(fill="x")
        for title, action in (("Проверить микрофон", "microphone"), ("Проверить динамики", "speaker")):
            button = self.button(tests, title, lambda action=action: self.run_setup(action))
            button.pack(side="left", padx=(0, 7))
            self.audio_buttons.append(button)
        self.update_audio_controls()

    def selected_audio(self):
        return {field: labels[combo.get()] for field, (combo, labels) in self.audio_values.items()}

    def update_audio_controls(self):
        if not self.audio_values:
            return
        saved = {field: self.audio_settings[field] for field in self.audio_values}
        changed = self.selected_audio() != saved
        restart = self.health.get("restart_required", False)
        if restart:
            message = "Перезапустите приложение, чтобы применить сохранённый выбор."
        elif changed:
            message = "Сохраните выбор перед проверкой устройств."
        else:
            message = self.audio_settings["message"] or self.health.get("audio_error", "")
        self.audio_hint.config(text=message)
        self.audio_save.config(state="normal" if changed and not self.busy else "disabled")
        for button in self.audio_buttons:
            button.config(state="normal" if not (changed or restart or self.busy)
                          and self.health.get("audio_device_ready") else "disabled")

    def build_journal(self, panel):
        toolbar = tk.Frame(panel, bg=PANEL)
        toolbar.pack(fill="x", pady=(0, 7))
        self.journal_previous = self.button(toolbar, "Назад", lambda: self.run_setup(
            "journal", max(0, self.journal_page["offset"] - JOURNAL_PAGE_SIZE)))
        self.journal_previous.pack(side="left")
        self.journal_next = self.button(toolbar, "Далее", lambda: self.run_setup(
            "journal", self.journal_page["offset"] + JOURNAL_PAGE_SIZE))
        self.journal_next.pack(side="left", padx=6)
        self.button(toolbar, "Обновить", lambda: self.run_setup("journal", 0)).pack(side="right")
        self.journal_position = self.label(toolbar, color=MUTED)
        self.journal_position.pack(side="left", padx=5)
        listing = tk.Frame(panel, bg=PANEL)
        listing.pack(fill="x")
        self.journal_rows = ttk.Treeview(listing, columns=("time", "question"), show="headings",
                                         height=6, selectmode="browse")
        self.journal_rows.heading("time", text="Время")
        self.journal_rows.heading("question", text="Вопрос")
        self.journal_rows.column("time", width=150, stretch=False)
        self.journal_rows.column("question", width=255)
        self.journal_rows.pack(side="left", fill="x", expand=True)
        rows_scroll = ttk.Scrollbar(listing, command=self.journal_rows.yview)
        rows_scroll.pack(side="right", fill="y")
        self.journal_rows.config(yscrollcommand=rows_scroll.set)
        self.journal_rows.bind("<<TreeviewSelect>>", self.show_journal_entry)
        details = tk.Frame(panel, bg=PANEL)
        details.pack(fill="both", expand=True, pady=(8, 0))
        self.journal_details = tk.Text(details, height=6, wrap="word", bg=PANEL, fg=INK,
                                       font=(FONT, 10), padx=6, pady=5, state="disabled")
        self.journal_details.pack(side="left", fill="both", expand=True)
        detail_scroll = ttk.Scrollbar(details, command=self.journal_details.yview)
        detail_scroll.pack(side="right", fill="y")
        self.journal_details.config(yscrollcommand=detail_scroll.set)
        self.render_journal_page()

    def update_journal_controls(self):
        page = self.journal_page
        self.journal_previous.config(state="normal" if page and page["offset"] and not self.busy else "disabled")
        self.journal_next.config(state="normal" if page and page["has_more"] and not self.busy else "disabled")
        self.journal_position.config(text=f"Страница {page['offset'] // JOURNAL_PAGE_SIZE + 1}" if page else "")

    def render_journal_page(self):
        self.journal_rows.delete(*self.journal_rows.get_children())
        rows = self.journal_page["items"] if self.journal_page else []
        for index, entry in enumerate(rows):
            try:
                stamp = datetime.fromisoformat(entry["created_at"]).astimezone().strftime("%d.%m.%Y %H:%M")
            except ValueError:
                stamp = entry["created_at"]
            question = " ".join(entry["question"].split()) or "Речь не распознана"
            self.journal_rows.insert("", "end", iid=str(index), values=(stamp, question))
        self.update_journal_controls()
        if rows:
            self.journal_rows.selection_set("0")
            self.show_journal_entry()
        else:
            self.set_journal_details("Записей за последние 30 дней пока нет." if self.journal_page
                                     else "Журнал загрузится при открытии вкладки.")

    def set_journal_details(self, text):
        self.journal_details.config(state="normal")
        self.journal_details.delete("1.0", "end")
        self.journal_details.insert("1.0", text)
        self.journal_details.config(state="disabled")

    def show_journal_entry(self, _event=None):
        selection = self.journal_rows.selection()
        if not self.journal_page or not selection:
            return
        entry = self.journal_page["items"][int(selection[0])]
        details = [
            f"Вопрос\n{entry['question'] or 'Речь не распознана'}",
            f"Ответ\n{entry['answer'] or 'Ответа нет'}",
            f"Озвученный текст\n{entry['spoken_text'] or 'Воспроизведение не зарегистрировано'}",
            f"Сервис · {entry['provider']}   Результат · {entry['status']}"
            + ("   Ответ прерван" if entry["interrupted"] else ""),
        ]
        for source in entry["sources"]:
            details.append(f"{source['document_title']} · {source['locator']}\n"
                           f"{source['source_label']}\n{source['text']}")
        self.set_journal_details("\n\n".join(details))

    def render_provider(self, panel):
        config = self.provider_settings
        if config is None:
            self.label(panel, "Загрузка настроек сервиса…", color=MUTED).pack(anchor="w")
            return
        selection = tk.StringVar(panel, config["provider"])
        model = tk.StringVar(panel, config["api_model"])
        fallback = tk.BooleanVar(panel, config["api_fallback"])
        row = tk.Frame(panel, bg=PANEL)
        row.pack(fill="x")
        for value, label in (("codex", "Codex"), ("api", "OpenAI API")):
            tk.Radiobutton(row, text=label, variable=selection, value=value, bg=PANEL,
                           activebackground=PALE, fg=INK, font=(FONT, 11)).pack(side="left", padx=(0, 14))

        self.label(panel, "Название модели API", bold=True).pack(fill="x", pady=(8, 3))
        tk.Entry(panel, textvariable=model, font=(FONT, 11)).pack(fill="x", ipady=4)
        fallback_checkbox = tk.Checkbutton(
            panel, text="Разрешить платный API при недоступности Codex", variable=fallback,
            bg=PANEL, activebackground=PALE, fg=INK, font=(FONT, 10),
        )
        fallback_checkbox.pack(anchor="w", pady=(5, 4))

        def update_fallback(*_args):
            fallback_checkbox.config(state="normal" if selection.get() == "codex" else "disabled")

        selection.trace_add("write", update_fallback)
        update_fallback()

        def save():
            if (selection.get() == "api" or fallback.get()) and not model.get().strip():
                self.settings_status.config(text="Укажите имя модели, доступной вашему API-проекту.")
                return
            self.run_setup("save_provider", {
                "provider": selection.get(), "api_model": model.get().strip(), "api_fallback": fallback.get(),
            })

        self.button(panel, "Сохранить выбор", save, primary=True).pack(anchor="w", pady=(4, 10))
        self.label(panel, "Новый API-ключ", bold=True).pack(fill="x", pady=(4, 3))
        key_row = tk.Frame(panel, bg=PANEL)
        key_row.pack(fill="x")
        key = tk.Entry(key_row, show="•", font=(FONT, 11))
        key.pack(side="left", fill="x", expand=True, ipady=4)

        def save_key():
            value = key.get().strip()
            if self.busy:
                return
            if not value or len(value.encode("utf-8")) > 1024:
                self.settings_status.config(text="Введите действительный API-ключ.")
                return
            key.delete(0, "end")
            self.run_setup("api_key", value)

        self.button(key_row, "Сохранить ключ", save_key).pack(side="right", padx=(8, 0))
        tk.Frame(panel, bg=LINE, height=1).pack(fill="x", pady=12)
        actions = tk.Frame(panel, bg=PANEL)
        actions.pack(fill="x")
        self.button(actions, "Проверить сохранённый сервис",
                    lambda: self.run_setup("probe")).pack(side="left")
        self.button(actions, "Войти в Codex",
                    lambda: self.run_setup("codex")).pack(side="left", padx=(7, 0))

    def choose_knowledge(self):
        file = filedialog.askopenfilename(parent=self.settings, filetypes=[("База знаний ZIP", "*.zip")])
        if file:
            self.run_setup("import", Path(file))

    def run_setup(self, action, value=None):
        if not self.busy:
            self.busy = True
            self.update_controls()
            self.settings_status.config(text="Говорите в микрофон ближайшие три секунды…"
                                        if action == "microphone" else "Подождите…")
            if self.admin:
                self.update_audio_controls()
                self.update_journal_controls()
            self.service.setup(action, value)


def main(data: Path, install: Path, *, kiosk: bool = False) -> None:
    window = tk.Tk()
    service = VoiceService(data, install / "examples/knowledge")
    try:
        VoiceWindow(window, service, install / "desktop/voice/avatar-atlas.png", kiosk=kiosk)
        window.mainloop()
    finally:
        service.close()
