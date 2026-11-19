# CampusAI — Cross-platform student workspace

CampusAI runs on Windows, macOS and Linux. It uses one Python/FastAPI backend and a browser-based interface.

## Features in this version
- Light and dark themes (saved in the browser).
- Student accounts with User ID and password.
- Passwords stored as salted PBKDF2-HMAC-SHA256 hashes, not plain text.
- First-login student profile setup (name and course).
- Private per-account weekly timetable.
- Add, edit and delete classes manually.
- AI timetable questions, including “What class do I have at 10:00 AM today?”.
- AI-proposed timetable changes require student approval.
- Campus tools: attendance target planner, weighted CGPA calculator and browser-saved study task list.
- Rebuilt sidebar: collapsible icon rail (remembered between visits), tooltips, mobile drawer with tap-outside/Esc to close, and a sticky page header.
- Click your profile photo (top right) to edit your profile. The photo can be uploaded, zoomed, repositioned and removed.
- Timetable extras: drag a class to an empty cell to move it (hold Ctrl, or Option on Mac, while dropping to copy it), copy a class to other days from its edit window, export the timetable as CSV (opens in Excel), and print it on one landscape page.
- Exams & deadlines tracker: save exams, quizzes, assignments and projects with a date, subject and notes. Dates only, no countdown. Tick items off as done.
- Accent colour picker (Profile page): 7 colours that work in light and dark mode, saved in the browser.
- Spreadsheet-style weekly timetable: days as rows, time slots as columns, merged cells for multi-slot classes, today's row highlighted. Click any cell to add, edit or delete a class.
- Windows launcher opens the local website automatically after the server is ready.
- Decision trace and evaluation view.
- Bionic API settings are configurable using `.env`.

## Important prototype notes
- This is a local project prototype, not a production university identity system.
- Accounts and timetable records are stored in a local SQLite database (`campusai.db`) on the machine running CampusAI. Accounts are not automatically shared between different installations or computers.
- The Bionic model server must be reachable from the machine running CampusAI. If Bionic is on another computer, set `BIONIC_BASE_URL` to that computer's reachable API address.
- Attendance, leave, library, lab and maintenance information is sample/demo functionality until connected to real university systems.
- For a public multi-user deployment, use HTTPS, secure session cookies, rate limiting, database backups, and an institution-approved authentication provider.

## Chatting and speed

- CampusAI now works like a chat assistant: it replies word by word as the model writes, remembers the conversation (use **New chat** to start fresh), and sends on Enter (Shift+Enter for a new line). Press **Stop** to cut a reply short.
- Normal questions go straight to the model in one streamed call. Timetable lookups are answered instantly without the model. Only requests that need campus tools (attendance, leave, labs, library, maintenance, changing classes) use the slower plan-then-run flow.
- Results are written in plain language, and changes still need your approval.
- If replies feel slow, open **Decision trace**: `chat_reply` shows the time to the first word and the total time, and `model_timing` shows the planning time. The `.env.example` file lists the speed options.

## Changing the AI model URL from the app

Click the **⚙** button in the top bar (or the connection status at the bottom of the sidebar) to open **AI model settings**. You can set the API URL, model name and API key, press **Test connection** (it lists the models your server offers and checks that it really answers), and **Save**. Settings are stored on this computer and override `.env`. If you forget `/v1`, the test fixes the URL for you.

If a chat shows "thinking" or an empty reply, keep **Disable thinking** ticked. If the model still thinks, CampusAI retries once with thinking forced off and explains clearly if it can't get an answer.

## Configure Bionic
Copy `.env.example` to `.env` and edit the values if needed:
- `BIONIC_BASE_URL=http://127.0.0.1:1235/v1` (use `127.0.0.1`, not `localhost`, which is slower on Windows)
- `BIONIC_MODEL=qwen3.5-4b`
- `BIONIC_API_KEY=` (leave blank if your local Bionic API does not require a key)

## Windows
1. Install Python 3.11 or 3.12 (recommended).
2. Extract this folder.
3. Copy `.env.example` to `.env` and configure Bionic if needed.
4. Double-click `run_windows.bat`.
5. The launcher starts the server and opens `http://127.0.0.1:8000` automatically.

## macOS
1. Install Python 3.11 or 3.12.
2. Open Terminal and enter this project folder.
3. Run `chmod +x run_mac_linux.sh` once.
4. Run `./run_mac_linux.sh`.
5. The launcher starts the server and opens `http://127.0.0.1:8000` automatically.

## Linux
1. Install Python 3.11 or 3.12 and the Python venv package for your distribution.
2. Open a terminal in this project folder.
3. Run `chmod +x run_mac_linux.sh` once.
4. Run `./run_mac_linux.sh`.
5. The launcher starts the server and opens `http://127.0.0.1:8000` automatically.

## Timetable and AI
- Open **Timetable** and click any cell of the grid to add/edit/delete your weekly classes (empty cells pre-fill the day and time).
- Class times use 24-hour time internally (for example, 13:30).
- Ask CampusAI “What class do I have at 10 AM on Monday?” or “Show my timetable for Friday.”
- Ask the AI to add/change a class. It will request your approval before changing saved timetable data.
- Time overlaps on the same day are rejected.

## Data and reset
The database file is `campusai.db` in this project folder. Back it up before moving the project. Deleting it resets local accounts, timetable records, demo data and evaluation history.


## Updated demo login and default timetable

- Sign in with any non-empty username and any password (the password is intentionally not validated in this local prototype). Reusing the same username opens the same local workspace.
- Name and course are optional; edit them by clicking your profile photo or name at the top right after login.
- Light/dark theme selection is available on both the login screen and inside the workspace. The selection is saved in the browser.
- New accounts with an empty timetable receive the default weekly schedule transcribed from the supplied LPU timetable PDF. Existing non-empty timetables are preserved.
- The frontend strips common `utm_*` tracking parameters, including `utm_source=chatgpt.com`, from the local page URL.

**Prototype security note:** this intentionally permissive sign-in is suitable only for a local demo. Do not expose this server to the public internet or use it for private/real student records.
