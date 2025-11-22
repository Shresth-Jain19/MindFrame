import os
import time
import logging
import glob
import cv2
import subprocess
from dotenv import load_dotenv
from supabase import create_client
from cryptography.fernet import Fernet
import yt_dlp
from faster_whisper import WhisperModel
import ollama
from newspaper import Article
import requests

# --- CONFIG ---
load_dotenv()
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

MAX_VIDEO_DURATION = 180  # 3 Minutes

supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))
cipher = Fernet(os.getenv("MASTER_KEY").encode())

# --- MODELS ---
print("⏳ Loading Whisper...")
whisper_model = WhisperModel("medium", device="cpu", compute_type="int8")
print("Model Ready.")


# --- 1. SMART SCENE DETECTION (Low Resource) ---
def extract_smart_frames(video_path, threshold=30.0):
    """
    Detects scene changes using histograms (Math) instead of AI.
    Only calls AI when the scene actually changes.
    """
    logging.info("👁️ Scanning video for scenes...")
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened():
        return ""

    visual_log = []
    prev_hist = None
    frame_count = 0
    last_saved_time = -5

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Check every 0.5 seconds (very cheap operation)
        if frame_count % int(fps * 0.5) == 0:
            current_time = frame_count / fps

            # Calculate Histogram (Color Profile)
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0], None, [256], [0, 256])
            cv2.normalize(hist, hist)

            is_new_scene = False
            if prev_hist is not None:
                # Math: Compare current frame colors to previous
                diff = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CHISQR)
                if diff > threshold and (current_time - last_saved_time > 2):
                    is_new_scene = True
            else:
                is_new_scene = True  # First frame

            if is_new_scene:
                # ONLY NOW do we use heavy resources
                try:
                    temp_frame = f"temp_frame_{int(current_time)}.jpg"
                    cv2.imwrite(temp_frame, frame)

                    logging.info(f"📸 Analyzing Scene @ {int(current_time)}s")
                    res = ollama.generate(
                        model="llava",
                        prompt="Read text and describe main object.",
                        images=[temp_frame],
                    )

                    if res["response"].strip():
                        visual_log.append(
                            f"[{int(current_time)}s]: {res['response'].strip()}"
                        )

                    last_saved_time = current_time
                    os.remove(temp_frame)
                except Exception:
                    pass

            prev_hist = hist

        frame_count += 1

    cap.release()
    return "\n".join(visual_log)


# --- 2. HELPER FUNCTIONS ---


def process_media(url):
    """Download and convert video to MP4 format, ensuring max duration."""
    # Enhanced options for better YouTube Shorts support
    ydl_opts = {
        "outtmpl": "temp_video.%(ext)s",
        "noplaylist": True,
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],  # Better Shorts support
            }
        },
    }

    try:
        # First, get video info to check duration
        info_opts = {
            "quiet": True,
            "no_warnings": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"],  # Better Shorts support
                }
            },
        }
        with yt_dlp.YoutubeDL(info_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            duration = info.get("duration", 0)

            if duration and duration > MAX_VIDEO_DURATION:
                logging.warning(
                    f"Video too long: {duration}s (max: {MAX_VIDEO_DURATION}s)"
                )
                return (
                    None,
                    f"Video too long ({duration // 60} min). Maximum allowed: 3 minutes.",
                )

        # Download video
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])

        # Find downloaded file
        downloaded_files = glob.glob("temp_video.*")
        if not downloaded_files:
            return None, "Download failed: No file found"

        video_path = downloaded_files[0]

        # Ensure it's MP4 format, convert if needed
        if not video_path.endswith(".mp4"):
            logging.info(f"Converting {video_path} to MP4...")
            mp4_path = "temp_video.mp4"
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-i",
                        video_path,
                        "-c:v",
                        "libx264",
                        "-c:a",
                        "aac",
                        "-y",
                        mp4_path,
                    ],
                    check=True,
                    capture_output=True,
                    timeout=300,
                )
                # Remove original file
                if os.path.exists(video_path):
                    os.remove(video_path)
                video_path = mp4_path
            except subprocess.TimeoutExpired:
                logging.error("FFmpeg conversion timed out")
                return None, "Video conversion timed out"
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                logging.error(f"FFmpeg conversion failed: {e}")
                # Try to use the original file anyway
                if video_path.endswith((".webm", ".mkv")):
                    return (
                        None,
                        "Video format not supported. Please ensure FFmpeg is installed.",
                    )

        # Verify file exists and is readable
        if not os.path.exists(video_path) or os.path.getsize(video_path) == 0:
            return None, "Downloaded file is empty or corrupted"

        return video_path, None

    except yt_dlp.utils.DownloadError as e:
        logging.error(f"YT-DLP Download Error: {e}")
        return None, f"Download failed: {str(e)}"
    except Exception as e:
        logging.error(f"Media processing error: {e}")
        return None, f"Error processing media: {str(e)}"


def cleanup():
    for f in glob.glob("temp_*"):
        try:
            os.remove(f)
        except:
            pass


def process_article(url):
    """Extract content from news articles."""
    try:
        article = Article(url)
        article.download()
        article.parse()

        content = f"""
Title: {article.title}
Text: {article.text[:5000]}  # Limit to first 5000 chars
Authors: {', '.join(article.authors) if article.authors else 'Unknown'}
Published: {article.publish_date}
"""
        return content
    except Exception as e:
        logging.error(f"Article processing error: {e}")
        try:
            # Fallback: simple text extraction
            response = requests.get(
                url, timeout=10, headers={"User-Agent": "Mozilla/5.0"}
            )
            return f"Article content (raw): {response.text[:3000]}"
        except:
            return "Failed to extract article content."


def generate_final_report(audio, visual, content_type="video"):
    if content_type == "article":
        prompt = f""" 
        
        Analyze this news article. 
        
        SOURCE DATA: 
        {audio}
        
        TASK: 
        1. Identify the category (e.g., Politics, Technology, Sports, Business, Health). 
        2. Extract the core value. 
        
        OUTPUT FORMAT (Strict): 
        **One-Line Overview:** (What is this article about?) 
        **Key Takeaways:** (Bullet points of the main facts, claims, or events mentioned). 
        **Important Details:** (Any dates, locations, names, statistics, or critical information). 
        
        """
    else:
        prompt = f""" 
        
        Analyze this Instagram Reel/Shorts content. 
        
        SOURCE DATA: 
        Audio: {audio} 
        Visuals: {visual} 
        
        TASK: 
        1. Identify the category (e.g., Food, Tech, Travel, Motivation, Humor). 
        2. Extract the core value. 
        
        OUTPUT FORMAT (Strict): 
        **One-Line Overview:** (What is this video about?) 
        **Key Takeaways:** (Bullet points of the main steps, ingredients, tips, or locations mentioned). 
        **Hidden Details / Actionable Info:** (Any prices, addresses, specific settings, software names, or warnings shown on screen but not spoken).
        
        """

    try:
        return ollama.chat(
            model="llama3.2", messages=[{"role": "user", "content": prompt}]
        )["message"]["content"]
    except Exception as e:
        logging.error(f"Report generation error: {e}")
        return "Error generating report."


# --- 3. MAIN LOOP (Sequential = Low RAM) ---
def main():
    print("🚀 MindFrame Online")
    cleanup()

    while True:
        try:
            # Heartbeat
            try:
                supabase.table("system_status").update({"last_seen": "now()"}).eq(
                    "id", 1
                ).execute()
            except:
                pass

            response = (
                supabase.table("tasks")
                .select("*")
                .eq("status", "pending")
                .limit(1)
                .execute()
            )

            if response.data:
                task = response.data[0]
                logging.info(f"⚡ Processing Task {task['id']}...")
                supabase.table("tasks").update({"status": "processing"}).eq(
                    "id", task["id"]
                ).execute()

                final_msg = ""
                task_url = task["content"].strip()

                # Determine if it's a video URL, article URL, or text
                is_url = task_url.startswith("http://") or task_url.startswith(
                    "https://"
                )
                task_url_lower = task_url.lower()
                is_video_url = is_url and any(
                    domain in task_url_lower
                    for domain in [
                        "youtube.com",
                        "youtu.be",
                        "instagram.com",
                        "tiktok.com",
                        "facebook.com",
                    ]
                )
                is_article_url = is_url and not is_video_url

                try:
                    if is_video_url:
                        # Process video
                        video_path, error_msg = process_media(task_url)

                        if error_msg:
                            final_msg = error_msg
                        elif video_path:
                            # Step A: Audio (CPU)
                            logging.info("🗣️ Extracting Audio...")
                            try:
                                segments, _ = whisper_model.transcribe(
                                    video_path, beam_size=5, task="translate"
                                )
                                audio_text = " ".join([s.text for s in segments])
                            except Exception as e:
                                logging.error(f"Whisper error: {e}")
                                audio_text = "Could not extract audio."

                            # Step B: Smart Vision (GPU - Sparse Calls)
                            visual_text = extract_smart_frames(video_path)

                            # Step C: Report
                            final_msg = generate_final_report(
                                audio_text, visual_text, "video"
                            )
                            cleanup()
                        else:
                            final_msg = "Download Failed: Could not process video."

                    elif is_article_url:
                        # Process article
                        logging.info("📰 Processing article...")
                        article_content = process_article(task_url)
                        final_msg = generate_final_report(
                            article_content, "", "article"
                        )

                    else:
                        # Plain text processing
                        final_msg = generate_final_report(
                            task_url, "No Visuals", "video"
                        )

                except Exception as e:
                    logging.error(f"Processing error for task {task['id']}: {e}")
                    final_msg = f"Error processing content: {str(e)}"

                # Encrypt and save result
                try:
                    encrypted = cipher.encrypt(final_msg.encode()).decode()
                    supabase.table("tasks").update(
                        {"encrypted_summary": encrypted, "status": "done"}
                    ).eq("id", task["id"]).execute()
                    logging.info(f"✅ Task {task['id']} Done.")
                except Exception as e:
                    logging.error(f"Failed to save result for task {task['id']}: {e}")
                    # Mark as done anyway to prevent infinite retry
                    supabase.table("tasks").update({"status": "done"}).eq(
                        "id", task["id"]
                    ).execute()

            else:
                time.sleep(2)

        except Exception as e:
            logging.error(f"Error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
