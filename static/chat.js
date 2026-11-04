"use strict";
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const transcript = document.querySelector("#transcript");
const send = document.querySelector("#send");
const stop = document.querySelector("#stop");
const status = document.querySelector("#status");
let controller;
let followOutput = true;

window.addEventListener("scroll", () => {
  followOutput = document.documentElement.scrollHeight - window.scrollY - window.innerHeight < 180;
}, {passive: true});

function resizeInput() {
  input.style.height = "auto";
  input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
}
input.addEventListener("input", resizeInput);
input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    form.requestSubmit();
  }
});

function scrollToOutput() {
  if (followOutput) window.scrollTo({top: document.documentElement.scrollHeight, behavior: "instant"});
}

function inlineMarkdown(parent, text) {
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\([^) \n]+\))/g;
  let offset = 0;
  for (const match of text.matchAll(pattern)) {
    parent.append(document.createTextNode(text.slice(offset, match.index)));
    const token = match[0];
    let node;
    if (token.startsWith("`")) {
      node = document.createElement("code");
      node.textContent = token.slice(1, -1);
    } else if (token.startsWith("**") || token.startsWith("*")) {
      const bold = token.startsWith("**");
      node = document.createElement(bold ? "strong" : "em");
      node.textContent = token.slice(bold ? 2 : 1, bold ? -2 : -1);
    } else {
      const link = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      let url;
      try { url = new URL(link[2]); } catch { /* Invalid links remain literal text. */ }
      if (url && ["https:", "http:"].includes(url.protocol) && !url.username && !url.password) {
        node = document.createElement("a");
        node.href = url.href;
        node.rel = "noopener noreferrer";
        node.target = "_blank";
        node.textContent = link[1];
      } else {
        node = document.createTextNode(token);
      }
    }
    parent.append(node);
    offset = match.index + token.length;
  }
  parent.append(document.createTextNode(text.slice(offset)));
}

// Model text only becomes allowlisted DOM nodes; raw HTML is always literal text.
function renderMarkdown(target, text) {
  const fragment = document.createDocumentFragment();
  let paragraph = [];
  let list;
  let code;
  function flush() {
    if (!paragraph.length) return;
    const p = document.createElement("p");
    inlineMarkdown(p, paragraph.join(" "));
    fragment.append(p);
    paragraph = [];
  }
  for (const line of text.replace(/\r\n/g, "\n").split("\n")) {
    if (line.startsWith("```")) {
      flush();
      list = undefined;
      if (code) {
        code = undefined;
      } else {
        const pre = document.createElement("pre");
        code = document.createElement("code");
        pre.append(code);
        fragment.append(pre);
      }
      continue;
    }
    if (code) {
      code.textContent += line + "\n";
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    const item = line.match(/^\s*(?:([-*])|(\d+)\.)\s+(.+)$/);
    if (!line.trim() || heading || item) flush();
    if (heading) {
      list = undefined;
      const h = document.createElement("h" + Math.min(heading[1].length + 1, 4));
      inlineMarkdown(h, heading[2]);
      fragment.append(h);
    } else if (item) {
      const type = item[2] ? "ol" : "ul";
      if (!list || list.tagName.toLowerCase() !== type) {
        list = document.createElement(type);
        fragment.append(list);
      }
      const li = document.createElement("li");
      inlineMarkdown(li, item[3]);
      list.append(li);
    } else {
      list = undefined;
      if (line.trim()) paragraph.push(line);
    }
  }
  flush();
  target.replaceChildren(fragment);
}

for (const button of document.querySelectorAll("[data-prompt]")) {
  button.addEventListener("click", () => {
    input.value = button.dataset.prompt;
    resizeInput();
    input.focus();
  });
}
stop.addEventListener("click", () => controller?.abort());

function message(kind, text) {
  const node = document.createElement("article");
  node.className = `message ${kind}`;
  const label = document.createElement("span");
  label.className = "message-label";
  label.textContent = kind === "user" ? "You" : "Assistant";
  const body = document.createElement("div");
  body.className = "message-body";
  body.textContent = text;
  node.append(label, body);
  transcript.append(node);
  return {node, body};
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (controller || !input.value.trim()) return;
  const text = input.value.trim();
  document.querySelector("#empty-state").hidden = true;
  followOutput = true;
  message("user", text);
  const reply = message("assistant", "Thinking...");
  reply.body.classList.add("pending");
  input.value = "";
  resizeInput();
  send.disabled = true;
  stop.hidden = false;
  status.textContent = "Receiving a response";
  controller = new AbortController();
  let reader;
  let finished = false;
  let failed = false;
  let output = "";
  function notice(text) {
    reply.node.classList.add("error");
    if (!output) reply.body.replaceChildren();
    reply.body.classList.remove("pending");
    const node = document.createElement("p");
    node.className = "notice";
    node.textContent = text;
    reply.node.append(node);
  }
  scrollToOutput();
  try {
    const response = await fetch("/api/chat/stream", {
      method: "POST", credentials: "same-origin", signal: controller.signal,
      headers: {"Content-Type": "application/json"}, body: JSON.stringify({message: text}),
    });
    if (!response.ok) {
      notice(response.status === 429
        ? "The demo is busy or rate-limited (HTTP 429). Wait a few seconds, then try again."
        : `The request wasn't accepted (HTTP ${response.status}). Check the demo window and try again.`);
      status.textContent = response.status === 429 ? "Please wait before sending again" : "Request not accepted";
      return;
    }
    if (!response.body) throw new Error("Missing stream");
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
          output += payload.text;
          reply.body.classList.remove("pending");
          renderMarkdown(reply.body, output);
        }
        if (type === "error") {
          failed = true;
          notice("The gateway request failed. No partial output was replayed.");
        }
        if (type === "done") finished = true;
      }
      scrollToOutput();
    }
    if (!finished) throw new Error("Incomplete stream");
    status.textContent = failed ? "Request failed" : "Response complete";
  } catch (error) {
    const aborted = error.name === "AbortError";
    notice(aborted
      ? (output ? "Stopped. Partial output was not retried." : "Stopped before model output.")
      : (output ? "The stream ended unexpectedly. Partial output was not retried."
        : "The request did not complete. Wait a few seconds and try again."));
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
