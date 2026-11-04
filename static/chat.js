"use strict";
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const transcript = document.querySelector("#transcript");
const send = document.querySelector("#send");
const stop = document.querySelector("#stop");
const status = document.querySelector("#status");
let controller;

for (const button of document.querySelectorAll("[data-prompt]")) {
  button.addEventListener("click", () => {
    input.value = button.dataset.prompt;
    input.focus();
  });
}
stop.addEventListener("click", () => controller?.abort());

function message(kind, text) {
  const node = document.createElement("p");
  node.className = `message ${kind}`;
  node.textContent = text;
  transcript.append(node);
  return node;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (controller || !input.value.trim()) return;
  const text = input.value.trim();
  message("user", text);
  const reply = message("assistant", "");
  input.value = "";
  send.disabled = true;
  stop.hidden = false;
  status.textContent = "Receiving a response";
  controller = new AbortController();
  let reader;
  let finished = false;
  let failed = false;
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({message: text}),
    });
    if (!response.ok || !response.body) throw new Error("Chat unavailable");
    reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream: true});
      if (buffer.length > 65536) throw new Error("Invalid stream");
      const events = buffer.split("\n\n");
      buffer = events.pop();
      for (const raw of events) {
        const type = raw.match(/^event: (\w+)$/m)?.[1];
        const data = raw.match(/^data: (.+)$/m)?.[1];
        if (!type || !data || finished) continue;
        const payload = JSON.parse(data);
        if (type === "delta") {
          if (typeof payload.text !== "string") throw new Error("Invalid stream");
          reply.textContent += payload.text;
        }
        if (type === "error") {
          failed = true;
          reply.className = "message error";
          reply.textContent += "\nThe gateway request failed. The response was not retried.";
        }
        if (type === "done") finished = true;
      }
      transcript.scrollTop = transcript.scrollHeight;
    }
    if (!finished) throw new Error("Incomplete stream");
    status.textContent = failed ? "Request failed" : "Response complete";
  } catch (error) {
    const aborted = error.name === "AbortError";
    reply.className = "message error";
    reply.textContent += aborted
      ? "\nStopped. Partial output was not retried."
      : "\nThe chat request did not complete. Partial output was not retried.";
    status.textContent = aborted ? "Stopped" : "Request failed";
  } finally {
    controller.abort();
    if (reader) reader.releaseLock();
    controller = undefined;
    send.disabled = false;
    stop.hidden = true;
    if (document.activeElement === send || document.activeElement === stop) input.focus();
  }
});
