# 🧠 MindFrame

An intelligent Telegram bot that extracts insights from short-form videos (Instagram Reels, YouTube Shorts) and news articles using AI-powered analysis. MindFrame processes video content through audio transcription and visual scene detection, then generates comprehensive summaries with key takeaways.

## ✨ Features

- **📹 Video Analysis**: Supports Instagram Reels, YouTube Shorts, TikTok, and Facebook videos
- **📰 Article Processing**: Extracts and summarizes news articles from any URL
- **🎤 Audio Transcription**: Uses Faster Whisper for accurate speech-to-text
- **👁️ Smart Scene Detection**: Efficiently analyzes visual content using histogram-based scene detection
- **🔒 Encrypted Storage**: All summaries are encrypted using Fernet encryption before storage
- **📊 Per-User History**: Track and retrieve your past summaries with `/history` and `/show` commands
- **⚡ Efficient Processing**: Low-resource architecture with sequential processing and smart caching
- **🔄 Real-time Status**: Monitors processor online/offline status
- **📱 Telegram Integration**: Seamless bot interface with automatic result delivery

## 🏗️ Architecture

MindFrame consists of two main components:

1. **`bot_interface.py`** - Telegram bot that handles user interactions
   - Receives video/article URLs from users
   - Queues tasks in Supabase database
   - Delivers completed summaries automatically
   - Manages user commands (`/start`, `/history`, `/show`)

2. **`processor.py`** - Video processing worker
   - Downloads videos using `yt-dlp`
   - Extracts audio transcripts with Faster Whisper
   - Analyzes visual content using scene detection + Ollama LLaVA
   - Generates summaries using Ollama Llama3.2
   - Encrypts and stores results in database

## 📋 Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai/) installed and running with:
  - `llama3.2` model
  - `llava` model
- FFmpeg installed (for video conversion)
- Supabase account and project
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- Fernet encryption key

## 🚀 Installation

1. **Clone the repository**
   ```bash
   git clone <https://github.com/Shresth-Jain19/MindFrame>
   cd MindFrame
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install FFmpeg**
   - **macOS**: `brew install ffmpeg`
   - **Ubuntu/Debian**: `sudo apt-get install ffmpeg`
   - **Windows**: Download from [FFmpeg website](https://ffmpeg.org/download.html)

4. **Set up Ollama models**
   ```bash
   ollama pull llama3.2
   ollama pull llava
   ```

5. **Create `.env` file**
   ```env
   # Telegram Bot
   TELEGRAM_BOT_TOKEN=your_telegram_bot_token_here

   # Supabase
   SUPABASE_URL=your_supabase_project_url
   SUPABASE_KEY=your_supabase_anon_key

   # Encryption
   MASTER_KEY=your_fernet_encryption_key_here
   ```

6. **Generate Fernet key** (if you don't have one)
   ```python
   from cryptography.fernet import Fernet
   print(Fernet.generate_key().decode())
   ```

## 🗄️ Database Setup

Create the required tables in your Supabase project using SQL Editor:

```sql
-- Create tasks table
CREATE TABLE IF NOT EXISTS tasks (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    user_name TEXT,
    content TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    encrypted_summary TEXT,
    task_number INTEGER DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT status_check CHECK (status IN ('pending', 'processing', 'done', 'archived'))
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_user_status ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_task_number ON tasks(user_id, task_number);

-- Create system_status table
CREATE TABLE IF NOT EXISTS system_status (
    id INTEGER PRIMARY KEY DEFAULT 1,
    last_seen TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Insert initial status
INSERT INTO system_status (id, last_seen)
VALUES (1, NOW())
ON CONFLICT (id) DO NOTHING;
```

## 🎮 Usage

### Starting the Bot

1. **Start the processor** (in one terminal):
   ```bash
   python processor.py
   ```

2. **Start the bot interface** (in another terminal):
   ```bash
   python bot_interface.py
   ```

### Bot Commands

- `/start` - Initialize the bot and get started
- `/history` - View your last 20 summaries
- `/show <id>` - View a specific summary by ID or task number

### Using the Bot

1. Send any YouTube Short, Instagram Reel, or article URL to the bot
2. Wait for processing (you'll see status updates)
3. Receive your summary automatically when ready
4. Use `/history` to see all your past summaries
5. Use `/show <id>` to retrieve a specific summary

### Supported URLs

- **Videos**: YouTube, Instagram, TikTok, Facebook
- **Articles**: Any news article URL (automatically detected)

## ⚙️ Configuration

### Video Duration Limit

Default maximum video duration is **3 minutes**. To change:

```python
# In processor.py
MAX_VIDEO_DURATION = 180  # seconds
```

### Whisper Model

Default model is `medium`. To change:

```python
# In processor.py
whisper_model = WhisperModel("medium", device="cpu", compute_type="int8")
```

Available options: `tiny`, `base`, `small`, `medium`, `large-v2`, `large-v3`

### Processing Interval

Background job checks for results every 4 seconds. To change:

```python
# In bot_interface.py
app.job_queue.run_repeating(check_results, interval=4, first=1)
```

## 🔒 Security

- All summaries are encrypted using **Fernet symmetric encryption** before storage
- Encryption key is stored in environment variables (never commit to git)
- User data is isolated per `user_id`
- Secure database connection via Supabase

## 🛠️ Technologies

- **Python 3.8+**
- **python-telegram-bot** - Telegram bot framework
- **Faster Whisper** - Fast speech recognition
- **Ollama** - Local LLM inference (Llama3.2, LLaVA)
- **yt-dlp** - Video downloading
- **OpenCV** - Video processing and scene detection
- **Supabase** - Database and backend
- **Fernet (cryptography)** - Encryption
- **newspaper3k** - Article extraction
- **FFmpeg** - Video conversion

## 📁 Project Structure

```
MindFrame/
├── bot_interface.py      # Telegram bot interface
├── processor.py          # Video/article processing worker
├── requirements.txt      # Python dependencies
└── README.md            # This file
```

## 🔍 How It Works

1. **User sends URL** → Bot queues task in database
2. **Processor picks up task** → Downloads video/article
3. **Audio extraction** → Faster Whisper transcribes speech
4. **Visual analysis** → Smart scene detection identifies key frames
5. **LLaVA analysis** → Analyzes detected scenes for visual content
6. **Summary generation** → Llama3.2 creates comprehensive summary
7. **Encryption** → Summary encrypted and stored
8. **Auto-delivery** → Bot automatically sends summary to user

## 🐛 Troubleshooting

### Bot not receiving messages
- Check if `TELEGRAM_BOT_TOKEN` is correct
- Verify bot is running and not crashed
- Check Supabase connection

### Videos not downloading
- Ensure `yt-dlp` is up to date: `pip install --upgrade yt-dlp`
- Check internet connection
- Verify URL format is correct

### Processing errors
- Check Ollama is running: `ollama list`
- Verify models are installed: `ollama pull llama3.2 && ollama pull llava`
- Check FFmpeg installation: `ffmpeg -version`
- Review logs for specific error messages

### Database errors
- Verify Supabase credentials in `.env`
- Check table schema matches expected structure
- Ensure database connection is stable

## 📝 Notes

- Processor runs sequentially (one task at a time) for low memory usage
- Scene detection uses histogram comparison for efficiency (only uses AI when scenes change)
- Video files are automatically cleaned up after processing
- Maximum video duration: 3 minutes (configurable)
- All summaries are encrypted at rest

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📄 License

[Your License Here]

## 🙏 Acknowledgments

- Built with [Ollama](https://ollama.ai/)
- Video processing powered by [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- Speech recognition by [Faster Whisper](https://github.com/guillaumekln/faster-whisper)

---

Made with ❤️ for extracting insights from short-form content

