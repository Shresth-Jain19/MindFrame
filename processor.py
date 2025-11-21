import os
import time
import logging
import threading
import glob
import cv2
from dotenv import load_dotenv
from supabase import create_client
from cryptography.fernet import Fernet
import yt_dlp
from faster_whisper import WhisperModel
import ollama
from newspaper import Article

# --- CONFIG ---
load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

MAX_VIDEO_DURATION = 180

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
cipher = Fernet(os.getenv("MASTER_KEY").encode())

# --- MODELS ---
print("⏳ Loading Whisper...")
whisper_model = WhisperModel("small", device="cpu", compute_type="int8")
print("✅ Model Ready.")


# --- HEARTBEAT (Background Thread) ---
def heartbeat():
    """Tells the cloud 'I am alive' every 10 seconds."""
    while True:
        try:
            supabase.table("system_status").update({"last_seen": "now()"}).eq(
                "id", 1
            ).execute()
        except Exception as e:
            logging.warning(f"Heartbeat failed: {e}")
        time.sleep(10)


threading.Thread(target=heartbeat, daemon=True).start()

# --- CORE FUNCTIONS ---


def get_video_info(url):
    """Checks duration BEFORE downloading."""
    ydl_opts = {"quiet": True, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            return info.get("duration", 0), info.get("title", "Unknown")
    except Exception:
        return 0, "Unknown"


def extract_article_text(url):
    """NEW: Scrapes text from blogs/news sites."""
    try:
        logging.info(f"📰 Attempting to read article: {url}")
        article = Article(url)
        article.download()
        article.parse()
        return article.text
    except Exception as e:
        logging.error(f"Article extraction failed: {e}")
        return None


def process_media(url):
    """Downloads video."""
    logging.info(f"⬇️ Downloading: {url}")
    ydl_opts = {
        "format": "best[ext=mp4]",
        "outtmpl": "temp_video.%(ext)s",
        "quiet": True,
        "noplaylist": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        return "temp_video.mp4"
    except Exception as e:
        logging.error(f"Download Error: {e}")
        return None


def extract_audio_text(video_path):
    segments, _ = whisper_model.transcribe(video_path, beam_size=5, task="translate")
    return " ".join([segment.text for segment in segments])


def extract_visual_context(video_path):
    """Extracts text/scenes from frames."""
    vidcap = cv2.VideoCapture(video_path)
    fps = vidcap.get(cv2.CAP_PROP_FPS)
    if fps == 0:
        return ""

    frame_interval = int(fps * 2)  # Check every 2s
    count = 0
    visual_data = []

    while True:
        success, image = vidcap.read()
        if not success:
            break

        if count % frame_interval == 0:
            frame_path = "temp_frame.jpg"
            cv2.imwrite(frame_path, image)
            try:
                # Quick OCR/Description
                res = ollama.generate(
                    model="llava",
                    prompt="Read any text on screen. Describe the main object. Be brief.",
                    images=[frame_path],
                )
                if len(res["response"]) > 5:
                    visual_data.append(f"[{int(count/fps)}s]: {res['response']}")
            except:
                pass
        count += 1

    vidcap.release()
    return "\n".join(visual_data)


def generate_generic_report(content_text, visual_context):
    """
    Universal Prompt: Handles Video Transcripts AND Article Text.
    """
    prompt = f"""
    Analyze this content (Video Transcript or Article Text).
    
    SOURCE DATA:
    Main Content / Audio: {content_text} 
    Visuals (If Video): {visual_context}
    
    TASK:
    1. Identify the category (e.g., News, Food, Tech, Travel, Opinion).
    2. Extract the core value.
    
    OUTPUT FORMAT (Strict):
    
    **One-Line Overview:** (What is this about?)
    
    **Summary:**
    (A concise paragraph summarizing the main points).
    
    **Key Takeaways / Details:**
    (Bullet points of the main steps, facts, arguments, or tips mentioned).
    """
    try:
        response = ollama.chat(
            model="llama3.2", messages=[{"role": "user", "content": prompt}]
        )
        return response["message"]["content"]
    except:
        return "Error generating summary."


def cleanup():
    """Deletes all temp files."""
    for f in glob.glob("temp_*"):
        try:
            os.remove(f)
        except:
            pass


# --- MAIN LOOP ---
def main():
    print("🚀 MindFrame Online")
    cleanup()  # Start clean

    while True:
        try:
            # Fetch pending task
            response = (
                supabase.table("tasks")
                .select("*")
                .eq("status", "pending")
                .limit(1)
                .execute()
            )

            if response.data:
                task = response.data[0]
                task_id = task["id"]
                content = task["content"]

                # Mark Processing
                supabase.table("tasks").update({"status": "processing"}).eq(
                    "id", task_id
                ).execute()

                final_msg = ""

                if "http" in content:
                    # 1. Check Duration FIRST (Is it a Video?)
                    duration, title = get_video_info(content)

                    if duration > 0:
                        # --- IT IS A VIDEO ---
                        if duration > MAX_VIDEO_DURATION:
                            final_msg = "⚠️ **Video too long.**\nMindFrame is optimized for Shorts/Reels (< 3 mins). Please send a shorter clip."
                        else:
                            # Process Video
                            video_path = process_media(content)
                            if video_path:
                                try:
                                    audio_text = extract_audio_text(video_path)
                                    visual_text = extract_visual_context(video_path)
                                    final_msg = generate_generic_report(
                                        audio_text, visual_text
                                    )
                                except Exception as e:
                                    final_msg = f"Processing Error: {str(e)}"
                                finally:
                                    cleanup()
                            else:
                                final_msg = "Error: Could not download video."
                    else:
                        # --- IT IS NOT A VIDEO (Try Article) ---
                        article_text = extract_article_text(content)
                        if article_text:
                            final_msg = generate_generic_report(
                                article_text, "No Visuals (Text Article)"
                            )
                        else:
                            final_msg = "❌ Error: Link is not a valid video or readable article."
                else:
                    # Text Input
                    final_msg = generate_generic_report(content, "None")

                # Encrypt & Finish
                encrypted = cipher.encrypt(final_msg.encode()).decode()
                supabase.table("tasks").update(
                    {"encrypted_summary": encrypted, "status": "done"}
                ).eq("id", task_id).execute()

                logging.info(f"Task {task_id} Done.")

            else:
                time.sleep(2)

        except Exception as e:
            logging.error(f"Error: {e}")
            cleanup()
            time.sleep(5)


if __name__ == "__main__":
    main()
