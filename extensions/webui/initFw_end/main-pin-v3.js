import { fetchApi } from "/js/api.js";

// The v3 module path invalidates browsers that cached the pre-bootstrap v2
// behavior, which installed no New Chat guard when the store was empty.
const PINNED_EXTENSION_NAME = "chat-history-main-pin-v3";
const PINNED_BADGE_CLASS = "chat-primary-pin";
const PINNED_BADGE_ICON = "keep";
const PINNED_LI_CLASS = "chat-pinned-main";
const PINNED_ENDPOINT = "/api/plugins/chat_history/pinned";
const LIST_SELECTOR = ".chats-config-list";
const ITEM_SELECTOR = "li.chat-tree-item";
const NAME_SELECTOR = ".chat-name";

let mainContextId = "";
let mainContextName = "";
let sidebarStoreRef = null;
let chatsStoreRef = null;
let observerInstalled = false;
let installed = false;
let selectionRepairPending = false;
let createGuardInstalled = false;
let stockNewChat = null;

async function fetchPinned() {
  try {
    const response = await fetchApi(PINNED_ENDPOINT, {
      method: "GET",
      credentials: "same-origin",
    });
    if (!response || !response.ok) return null;
    let payload;
    try {
      payload = await response.json();
    } catch (_e) {
      return null;
    }
    if (!payload || !payload.ok || !payload.pin_main_chat) return null;
    const contextId = payload.context_id;
    if (!contextId || typeof contextId !== "string") return null;
    return {
      contextId,
      name: typeof payload.name === "string" ? payload.name : "",
    };
  } catch (_e) {
    return null;
  }
}

function pinnedSort(rows) {
  if (!mainContextId || !Array.isArray(rows)) return rows;
  const pinned = [];
  const rest = [];
  for (const row of rows) {
    if (!row) continue;
    if (row.id === mainContextId) pinned.push(row);
    else if (row.parent_context_id) rest.push(row);
  }
  return pinned.concat(rest);
}

function registerSortExtension() {
  if (!sidebarStoreRef || typeof sidebarStoreRef.registerRowListExtension !== "function") return;
  sidebarStoreRef.registerRowListExtension("chat", PINNED_EXTENSION_NAME, {
    sort: pinnedSort,
  });
}

function applyPinned(pinned) {
  if (!pinned) return false;
  mainContextId = pinned.contextId;
  mainContextName = pinned.name || "";
  registerSortExtension();
  installObserver();
  onListMutated();
  updateNewChatButton();
  return true;
}

async function refreshPinned(attempts = 1) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    const pinned = await fetchPinned();
    if (applyPinned(pinned)) return pinned;
    if (attempt + 1 < attempts) {
      await new Promise((resolve) => globalThis.setTimeout(resolve, 100));
    }
  }
  return null;
}

function findPinnedRow(listEl) {
  if (!listEl) return null;
  const items = listEl.querySelectorAll(`:scope > ${ITEM_SELECTOR}`);
  for (const li of items) {
    if (li.classList.contains(PINNED_LI_CLASS)) return li;
    if (li.dataset && li.dataset.contextId === mainContextId) return li;
    const nameEl = li.querySelector(NAME_SELECTOR);
    if (mainContextName && nameEl && (nameEl.textContent || "").trim() === mainContextName) {
      return li;
    }
  }
  return null;
}

function applyBadge(listEl) {
  if (!listEl || !mainContextId) return;
  const pinned = findPinnedRow(listEl);
  if (!pinned) return;
  const button = pinned.querySelector(":scope > .chat-container > .chat-list-button");
  if (button) {
    let badge = button.querySelector(`:scope > .${PINNED_BADGE_CLASS}`);
    if (!badge) {
      badge = document.createElement("span");
      badge.className = `material-symbols-outlined ${PINNED_BADGE_CLASS}`;
      badge.textContent = PINNED_BADGE_ICON;
      badge.setAttribute("aria-label", "Pinned Main chat");
      badge.setAttribute("title", "Pinned Main chat");
      const nameEl = button.querySelector(NAME_SELECTOR);
      if (nameEl && nameEl.parentNode === button) {
        nameEl.insertAdjacentElement("beforebegin", badge);
      } else {
        button.appendChild(badge);
      }
    }
  }
}

function reorderList(listEl) {
  if (!listEl || !mainContextId) return;
  const pinned = findPinnedRow(listEl);
  if (!pinned) return;
  if (listEl.firstElementChild !== pinned) {
    listEl.insertBefore(pinned, listEl.firstElementChild);
  }
}

function onListMutated() {
  const listEl = document.querySelector(LIST_SELECTOR);
  if (!listEl) return;
  reorderList(listEl);
  applyBadge(listEl);
  repairHiddenSelection();
}

function installObserver() {
  if (observerInstalled) return;
  const listEl = document.querySelector(LIST_SELECTOR);
  if (!listEl || typeof MutationObserver === "undefined") return;
  const observer = new MutationObserver(() => onListMutated());
  observer.observe(listEl, { childList: true, subtree: true });
  document.addEventListener("webui-extensions-loaded", () => onListMutated(), { once: true });
  observerInstalled = true;
  onListMutated();
}

async function resolveSidebarStore() {
  try {
    const mod = await import("/components/sidebar/sidebar-store.js");
    sidebarStoreRef = mod && mod.store ? mod.store : null;
  } catch (_e) {
    sidebarStoreRef = null;
  }
}

async function resolveChatsStore() {
  try {
    const mod = await import("/components/sidebar/chats/chats-store.js");
    chatsStoreRef = mod && mod.store ? mod.store : null;
  } catch (_e) {
    chatsStoreRef = null;
  }
}

function isVisibleContext(contextId) {
  if (!contextId || !chatsStoreRef || !Array.isArray(chatsStoreRef.contexts)) return false;
  const row = chatsStoreRef.contexts.find((context) => context && context.id === contextId);
  return Boolean(
    row && (row.id === mainContextId || row.parent_context_id === mainContextId),
  );
}

async function selectMain() {
  if (!mainContextId || !chatsStoreRef || typeof chatsStoreRef.selectChat !== "function") {
    return mainContextId || null;
  }
  await chatsStoreRef.selectChat(mainContextId);
  return mainContextId;
}

function updateNewChatButton() {
  const button = document.getElementById("newChat");
  if (!button) return;
  const label = mainContextId ? "Open Main chat" : "Start Main chat";
  button.setAttribute("aria-label", label);
  button.setAttribute("title", label);
}

function installSingleChatCreateGuard() {
  if (
    createGuardInstalled
    || !chatsStoreRef
    || typeof chatsStoreRef.newChat !== "function"
  ) return;

  stockNewChat = chatsStoreRef.newChat.bind(chatsStoreRef);
  chatsStoreRef.newChat = async (...args) => {
    if (mainContextId) return selectMain();

    const createdContextId = await stockNewChat(...args);
    if (!createdContextId) return null;

    // /chat_create has already registered the live AgentContext. Resolving the
    // pin now claims that first root chat as Main; the short retry covers the
    // WebSocket snapshot arriving just after the HTTP response.
    await refreshPinned(10);
    return mainContextId || createdContextId;
  };
  createGuardInstalled = true;
  updateNewChatButton();
}

function repairHiddenSelection() {
  if (selectionRepairPending || !chatsStoreRef || !mainContextId) return;
  if (!Array.isArray(chatsStoreRef.contexts) || !chatsStoreRef.contexts.length) return;
  const selected = chatsStoreRef.selected || "";
  // Empty selection is the stock dashboard/home state, not a broken chat.
  if (!selected) return;
  if (isVisibleContext(selected)) return;

  selectionRepairPending = true;
  Promise.resolve(selectMain()).finally(() => {
    selectionRepairPending = false;
  });
}

export default async function initChatHistoryMainPin() {
  if (installed) return;
  installed = true;

  await Promise.all([resolveSidebarStore(), resolveChatsStore()]);
  registerSortExtension();
  installSingleChatCreateGuard();
  installObserver();
  await refreshPinned();
  repairHiddenSelection();
}
