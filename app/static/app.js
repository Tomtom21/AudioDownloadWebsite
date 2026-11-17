// Getting our buttons adn message elements
const form = document.querySelector("#download-form");
const input = document.querySelector("#media-url");
const button = document.querySelector("#download-button");
const statusMessage = document.querySelector("#status");

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  // Show progress and prevent duplicate submissions while the request is running.
  button.disabled = true;
  button.querySelector(".button-label").textContent = "Preparing...";
  statusMessage.classList.remove("error");
  statusMessage.textContent = "Downloading audio — This may take a sec.";

  try {
    // Send the entered media URL to the server for conversion.
    const response = await fetch("/download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: input.value.trim() }),
    });

    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.detail || "The link could not be downloaded.");
    }

    // Turn the audio response into a temporary link that triggers a file download.
    const audio = await response.blob();
    const downloadUrl = URL.createObjectURL(audio);

    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = "audio.mp3";
    document.body.append(link);
    link.click();
    link.remove();

    window.setTimeout(() => URL.revokeObjectURL(downloadUrl), 60_000);
    statusMessage.textContent = "Your audio is ready..";
  } catch (error) {
    // Display server or network errors beside the form.
    statusMessage.textContent = error.message || "Something went wrong. Please try again.";
    statusMessage.classList.add("error");
  } finally {
    // Allow another download whether this attempt succeeded or failed.
    button.disabled = false;
    button.querySelector(".button-label").textContent = "Download";
  }
});
