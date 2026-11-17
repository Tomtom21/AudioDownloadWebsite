A self-hosted website for downloading audio as MP3 using yt-dlp.

## Run with Docker

Build and start the site:

```sh
docker compose up --build -d
```

Open [http://localhost:3001](http://localhost:3001). The site accepts public HTTP/HTTPS links supported by `yt-dlp`, converts the audio to 192 kbps MP3, and downloads it as `audio.mp3`.

To stop it:

```sh
docker compose down
```

## Limits and safety

- One conversion runs at a time; links to local/private network addresses are rejected.
- Downloads are limited to 256 MB and four hours.
- There is currently no authentication within this application. I wouldn't recommend publicly exposing it.
