const form = document.querySelector("form");
const input = document.querySelector("input");
const button = document.querySelector("button");
const error = document.querySelector("#error");

window.reasoningConnection
  .status()
  .then((status) => {
    input.value = status.origin || "";
    error.textContent = status.error || "";
  })
  .catch(() => {
    error.textContent =
      "Connection settings are unavailable. Restart Reasoning.";
  });

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  button.disabled = true;
  error.textContent = "";
  try {
    const result = await window.reasoningConnection.connect(input.value);
    if (!result.ok) error.textContent = result.error;
  } catch {
    error.textContent =
      "Could not connect. Check the server URL and try again.";
  } finally {
    button.disabled = false;
  }
});
