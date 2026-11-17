import ipaddress
import shutil
import socket
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

# Keep request sizes and download work bounded for this small web service.
APP_DIR = Path(__file__).parent
MAX_URL_LENGTH = 2048
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
MAX_DURATION_SECONDS = 4 * 60 * 60
DOWNLOAD_SLOT = threading.Lock()

app = FastAPI(title="Audio Download")
# Serve the browser client separately from the route that returns its HTML entry point.
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


class DownloadRequest(BaseModel):
    # Validate the request body and reject oversized URLs before starting any download work.
    url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)


def validate_url(url: str) -> None:
    """Allow only public HTTP(S) URLs before passing them to the downloader."""
    if len(url) > MAX_URL_LENGTH:
        raise HTTPException(status_code=422, detail="The link is too long.")

    # Parse a trimmed URL and convert malformed URLs or ports into a client-facing error.
    try:
        parsed = urlsplit(url.strip())
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise HTTPException(status_code=422, detail="Enter a valid public link.") from error

    # Embedded credentials and non-HTTP schemes are unnecessary for public media links.
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username
        or parsed.password
        or "%" in hostname
    ):
        raise HTTPException(status_code=422, detail="Enter a valid public http or https link.")

    hostname = hostname.rstrip(".").lower()
    # Block local hostnames before DNS lookup, including common internal suffixes.
    if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
        raise HTTPException(status_code=422, detail="Links to local network addresses are not allowed.")

    # Resolve every address so a hostname with both public and private results is rejected.
    try:
        addresses = {
            result[4][0].split("%", 1)[0]
            for result in socket.getaddrinfo(
                hostname,
                port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        }
    except socket.gaierror as error:
        raise HTTPException(status_code=422, detail="The link host could not be resolved.") from error

    # Reject the host if any DNS result points to a non-public address.
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise HTTPException(status_code=422, detail="Links to local network addresses are not allowed.")


def enforce_download_limit(status: dict) -> None:
    """Stop the download if reported progress exceeds the configured byte limit."""
    # Abort as soon as reported download progress exceeds the configured byte limit.
    if status.get("status") == "downloading" and status.get("downloaded_bytes", 0) > MAX_DOWNLOAD_BYTES:
        raise yt_dlp.utils.DownloadError("The audio exceeds the 256 MB download limit.")


@app.get("/")
def home() -> FileResponse:
    """Return the web interface entry point."""
    return FileResponse(APP_DIR / "static" / "index.html")


@app.get("/healthz")
def health() -> dict:
    """Provide a lightweight endpoint for service health checks."""
    return {"status": "ok"}


@app.post("/download")
def download_audio(request: DownloadRequest) -> FileResponse:
    """Download one public media URL and return its audio as an MP3 file."""
    validate_url(request.url)

    # Fail fast rather than queueing requests that could consume additional resources.
    if not DOWNLOAD_SLOT.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A download is already in progress. Try again shortly.")

    # Keep each request's source and converted output isolated in its own temporary directory.
    workdir = Path(tempfile.mkdtemp(prefix="audio-download-"))
    
    # Once returned, the response owns cleanup; failures are cleaned up in finally.
    handed_off = False

    try:
        output_path = workdir / "audio.mp3"
        # Fetch a single audio item without playlist expansion, then convert it to the API's MP3 output.
        options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 20,
            "retries": 2,
            "fragment_retries": 2,
            # Apply an early size check and a duration filter before processing long media.
            "max_filesize": MAX_DOWNLOAD_BYTES,
            "match_filter": yt_dlp.utils.match_filter_func(
                f"duration <= {MAX_DURATION_SECONDS}"
            ),
            # Give the postprocessor and response a predictable output location.
            "outtmpl": str(workdir / "audio.%(ext)s"),
            "progress_hooks": [enforce_download_limit],
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }

        # yt-dlp handles source extraction; FFmpeg performs the configured audio conversion.
        with yt_dlp.YoutubeDL(options) as downloader:
            downloader.download([request.url.strip()])

        # A successful downloader return does not guarantee that conversion produced the expected file.
        if not output_path.is_file():
            raise HTTPException(
                status_code=422,
                detail="No audio file was produced. Check the link and try another source.",
            )

        response = FileResponse(
            output_path,
            media_type="audio/mpeg",
            filename="audio.mp3",
            # Remove temporary source and output files after the response has finished sending.
            background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
        )
        handed_off = True
        return response
    except yt_dlp.utils.DownloadError as error:
        # Replace FFmpeg details with a stable message and keep other errors bounded for the client.
        message = str(error).strip()
        if "ffmpeg" in message.lower():
            message = "Audio conversion failed. Please try again later."
        raise HTTPException(status_code=422, detail=message[:300] or "The link could not be downloaded.") from error
    finally:
        # Delete files immediately on failure; always release the single-download slot.
        if not handed_off:
            shutil.rmtree(workdir, ignore_errors=True)
        DOWNLOAD_SLOT.release()
